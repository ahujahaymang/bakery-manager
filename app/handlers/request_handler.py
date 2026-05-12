"""
Platform-agnostic request handler.

Owns the agent loop, conversation history, and image processing logic.
Works with any messaging platform — Telegram, WhatsApp, Slack, etc.
The caller supplies only: chat_id and raw input (text or image).
The handler resolves the correct DB session internally from tenant_id,
ensuring the right database is always used regardless of admin switching.
"""

import logging
from collections import defaultdict
from contextlib import contextmanager
from typing import Dict, List, Optional
from uuid import UUID

from app.services.agent_service import AgentService
from app.services.tool_executor import ToolExecutor
from app.services.image_service import ImageService
from app.services.llm_service import LLMService
from app.services.admin_notifier import AdminNotifier
from app.config import settings

logger = logging.getLogger(__name__)

# Number of message turns kept in memory per conversation
MAX_HISTORY = 20


@contextmanager
def _open_db(tenant_id: UUID):
    """Open a business DB session for tenant_id and ensure it is closed."""
    from app.database import get_db
    db = next(get_db(tenant_id))
    try:
        yield db
    finally:
        db.close()


class RequestHandler:
    """
    Platform-agnostic handler that runs the LLM agent for every request.

    Responsibilities:
    - Maintain per-chat conversation history (in-memory, last MAX_HISTORY turns)
    - Route text messages through the LLM agent
    - Process images: extract structured data, summarise, pass to agent
    - Handle admin impersonation for multi-tenant management
    - Return a plain string response for the caller to deliver

    The caller (telegram_listener, whatsapp_listener, etc.) only handles
    platform I/O: receiving messages, downloading files, sending replies.
    """

    def __init__(
        self,
        admin_notifier: Optional[AdminNotifier] = None,
        instagram_listener=None,
    ):
        self.agent = AgentService()
        self.llm_service = LLMService()
        self.image_service = ImageService(self.llm_service)
        self.instagram_listener = instagram_listener
        self.admin_notifier = admin_notifier or AdminNotifier(
            admin_chat_id=settings.ADMIN_CHAT_ID or ""
        )
        # Per-chat message history: {chat_id: [{role, content}, ...]}
        self._history: Dict[str, List[dict]] = defaultdict(list)
        # Admin tenant override: {admin_chat_id: tenant_id}
        self._admin_target: Dict[str, UUID] = {}
        # Last error per chat — stored for /report command
        self._last_error: Dict[str, dict] = {}
        # Pending images awaiting type clarification: {chat_id: image_bytes}
        self._pending_images: Dict[str, bytes] = {}

    # Per-chat onboarding state: tracks chats awaiting business name input
    # {chat_id: True}  — simple flag, no expiry needed (cleared on name save)
    _awaiting_business_name: Dict[str, bool] = {}
    # {chat_id: True} — waiting for country after business name
    _awaiting_country: Dict[str, bool] = {}

    # ── History ────────────────────────────────────────────────────────────

    def _get_history(self, chat_id: str) -> List[dict]:
        """Return the conversation history for a chat."""
        return self._history[chat_id]

    def _append(self, chat_id: str, role: str, content: str) -> None:
        """Append a message to history, trimming to MAX_HISTORY."""
        history = self._history[chat_id]
        history.append({"role": role, "content": content})
        if len(history) > MAX_HISTORY:
            self._history[chat_id] = history[-MAX_HISTORY:]

    # ── Public API ─────────────────────────────────────────────────────────

    async def handle_error(
        self,
        chat_id: str,
        user_message: str,
        error: Exception,
        business_name: Optional[str] = None,
    ) -> str:
        """
        Called by the platform adapter when an unhandled exception occurs.

        Stores the error context for /report, notifies admin, and returns
        a user-friendly message with a /report hint.
        """
        # Store for /report
        self._last_error[chat_id] = {
            "message": user_message,
            "error": error,
            "business_name": business_name,
        }

        # Notify admin asynchronously — don't let this block the user response
        try:
            await self.admin_notifier.notify_error(
                chat_id=chat_id,
                user_message=user_message,
                error=error,
                business_name=business_name,
            )
        except Exception as notify_err:
            logger.error(f"Failed to notify admin of error: {notify_err}")

        return (
            "⚠️ Something went wrong while processing your request.\n\n"
            "Please try again. If the problem persists, use */report* to let us know."
        )

    async def handle_text(self, tenant_id: UUID, chat_id: str, text: str) -> str:
        """
        Process a text message and return the agent's response.

        The handler opens its own DB session for the correct tenant.
        Callers only need to supply tenant_id and chat_id.

        Onboarding flow for new users:
        1. First message → ask for business name
        2. Business name reply → save it, show capabilities

        Admin flow:
        - /switch command → list or switch active tenant
        - Other messages → route to configured tenant with label

        Regular flow:
        - Run agent with conversation history

        Args:
            tenant_id: Tenant UUID (resolved from chat_id by the caller)
            chat_id: Unique conversation identifier
            text: User's message text

        Returns:
            Response string to deliver to the user
        """
        if self._is_admin(chat_id):
            return await self._handle_admin_message(tenant_id, chat_id, text)

        # Privacy command — available to all users at any time
        if text.lower() in ("/privacy", "privacy policy", "data privacy"):
            return (
                "🔒 *Your data is private and secure.*\n\n"
                "• Your business data is stored in an encrypted private database\n"
                "• Your data is never shared with other businesses or sold to advertisers\n"
                "• Messages are processed by OpenAI to understand your requests — OpenAI does not train on API data\n"
                "• You can request deletion of all your data at any time\n\n"
                "To request account deletion, contact us and we will process it within 7 days."
            )

        # /report — send last error context to admin
        if text.lower() == "/report":
            return await self._handle_report_command(chat_id)

        # /feedback <text> — send feedback to admin
        if text.lower().startswith("/feedback"):
            return await self._handle_feedback_command(chat_id, text, tenant_id)

        # /request <text> — send feature request to admin
        if text.lower().startswith("/request"):
            return await self._handle_request_command(chat_id, text, tenant_id)

        # Pending image clarification — user replied with image type
        if chat_id in self._pending_images:
            image_type = self._detect_image_type(text)
            if image_type:
                image_bytes = self._pending_images.pop(chat_id)
                return await self.handle_image(tenant_id, chat_id, image_bytes, text)
            else:
                return (
                    "Please reply with one of:\n"
                    "• *recipe* — handwritten or printed recipe\n"
                    "• *receipt* — payment receipt or bill\n"
                    "• *order* — WhatsApp/SMS order screenshot"
                )

        # Step 1: brand new user — no history, no business name set yet
        if not self._get_history(chat_id) and chat_id not in self._awaiting_business_name:
            already_named = await self._get_business_name(tenant_id)
            if already_named:
                self._append(chat_id, "assistant", f"Welcome back, {already_named}!")
            else:
                self._awaiting_business_name[chat_id] = True
                return (
                    "👋 Welcome! I'm your business operations assistant.\n\n"
                    "Before we get started — *what's the name of your business?*\n\n"
                    "_(e.g. Priya's Kitchen, Sweet Treats by Meena)_"
                )

        # Step 2: awaiting business name — save it and ask for country
        if chat_id in self._awaiting_business_name:
            return await self._save_business_name(tenant_id, chat_id, text)

        # Step 3: awaiting country — save it and show capabilities
        if chat_id in self._awaiting_country:
            return await self._complete_onboarding(tenant_id, chat_id, text)

        # Subscription gate — check before running agent
        gate_response = await self._check_subscription(tenant_id, chat_id)
        if gate_response:
            return gate_response

        # Instagram order confirmation — check before running agent
        if self.instagram_listener and self.instagram_listener.has_pending_confirmation(chat_id):
            result = await self.instagram_listener.handle_confirmation(chat_id, tenant_id, text)
            if result is not None:
                return result

        with _open_db(tenant_id) as db:
            return await self._run_agent(db, tenant_id, chat_id, text)

    async def _get_business_name(self, tenant_id: UUID) -> Optional[str]:
        """Look up the business name for a tenant from the registry. Returns None if not set."""
        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
            return tenant.business_name if tenant and tenant.business_name else None
        except Exception:
            return None
        finally:
            reg_db.close()

    async def _save_business_name(
        self, tenant_id: UUID, chat_id: str, business_name: str
    ) -> str:
        """Save business name and ask for country."""
        from app.services.tenant_service import TenantService
        from app.database import get_registry_db

        reg_db = next(get_registry_db())
        try:
            TenantService(reg_db).set_business_name(tenant_id, business_name)
        finally:
            reg_db.close()

        del self._awaiting_business_name[chat_id]
        self._awaiting_country[chat_id] = True
        self._append(chat_id, "user", business_name)

        return (
            f"Great! *{business_name.strip()}* is noted.\n\n"
            "Which country are you based in?\n\n"
            "_(This helps us use the right currency on invoices)_\n"
            "Examples: India, US, UK, UAE, Canada, Australia"
        )

    async def _complete_onboarding(
        self, tenant_id: UUID, chat_id: str, country: str
    ) -> str:
        """Save country and show the welcome/capabilities message."""
        from app.services.tenant_service import TenantService
        from app.database import get_registry_db

        reg_db = next(get_registry_db())
        try:
            svc = TenantService(reg_db)
            svc.set_country(tenant_id, country)
            tenant = svc.get_tenant_by_id(tenant_id)
            business_name = tenant.business_name or "your business"
            currency = TenantService.currency_for_country(country)
        finally:
            reg_db.close()

        del self._awaiting_country[chat_id]
        self._append(chat_id, "user", country)

        # Do NOT auto-start trial — admin must approve first
        # Notify admin that a new user has completed onboarding
        await self._notify_admin_new_signup(tenant_id, business_name, country)

        welcome = (
            f"✅ *{business_name}* is registered!\n"
            f"Currency: *{currency}*\n\n"
            "Your account is pending approval. You'll receive a message once your "
            "free trial is activated — usually within a few hours."
        )
        self._append(chat_id, "assistant", welcome)
        return welcome

    async def _notify_admin_new_signup(
        self, tenant_id: UUID, business_name: str, country: str
    ) -> None:
        """Notify admin of a new signup via AdminNotifier."""
        if not settings.ADMIN_CHAT_ID:
            return

        from app.database import get_registry_db
        from app.models import Tenant
        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
            chat_id = tenant.chat_id if tenant else str(tenant_id)
        finally:
            reg_db.close()

        try:
            await self.admin_notifier.notify_new_signup(
                chat_id=chat_id,
                business_name=business_name,
                country=country,
            )
        except Exception as e:
            logger.warning(f"Could not notify admin of new signup: {e}")

    # ── Owner commands ─────────────────────────────────────────────────────

    async def _handle_report_command(self, chat_id: str) -> str:
        """
        /report — send the last error context to admin.
        Useful when the owner wants to manually escalate an issue.
        """
        last = self._last_error.get(chat_id)
        if not last:
            return (
                "ℹ️ No recent error to report.\n\n"
                "If you're experiencing an issue, describe it and I'll try to help."
            )

        try:
            await self.admin_notifier.notify_error(
                chat_id=chat_id,
                user_message=last["message"],
                error=last["error"],
                business_name=last.get("business_name"),
            )
        except Exception as e:
            logger.error(f"Failed to send error report: {e}")

        return (
            "✅ Your error report has been sent to the admin.\n\n"
            "We'll look into it and get back to you."
        )

    async def _handle_feedback_command(
        self, chat_id: str, text: str, tenant_id: UUID
    ) -> str:
        """
        /feedback <text> — send feedback to admin.
        """
        feedback_text = text[len("/feedback"):].strip()
        if not feedback_text:
            return (
                "📝 Please include your feedback after the command.\n\n"
                "Example: `/feedback The invoice PDF looks great!`"
            )

        business_name = await self._get_business_name(tenant_id)
        try:
            await self.admin_notifier.notify_feedback(
                chat_id=chat_id,
                feedback_text=feedback_text,
                business_name=business_name,
            )
        except Exception as e:
            logger.error(f"Failed to send feedback: {e}")

        return "✅ Thank you for your feedback! We really appreciate it."

    async def _handle_request_command(
        self, chat_id: str, text: str, tenant_id: UUID
    ) -> str:
        """
        /request <text> — send a feature request to admin.
        """
        request_text = text[len("/request"):].strip()
        if not request_text:
            return (
                "💡 Please describe the feature after the command.\n\n"
                "Example: `/request Add support for bulk order imports`"
            )

        business_name = await self._get_business_name(tenant_id)
        try:
            await self.admin_notifier.notify_feature_request(
                chat_id=chat_id,
                request_text=request_text,
                business_name=business_name,
            )
        except Exception as e:
            logger.error(f"Failed to send feature request: {e}")

        return (
            "💡 Your feature request has been logged!\n\n"
            "We review all requests and prioritise based on demand. "
            "Thank you for helping us improve."
        )

    async def _check_subscription(self, tenant_id: UUID, chat_id: str) -> Optional[str]:
        """
        Check subscription status. Returns a blocking message if access is denied,
        or None if access is allowed.
        """
        from app.services.tenant_service import TenantService
        from app.database import get_registry_db

        reg_db = next(get_registry_db())
        try:
            svc = TenantService(reg_db)
            status = svc.check_and_update_status(tenant_id)

            if status == "pending":
                # Onboarding not complete — shouldn't reach here normally
                return None

            if status in ("trial", "active"):
                days = svc.days_remaining(tenant_id)
                # Warn when 2 days left in trial
                if status == "trial" and days is not None and days <= 2:
                    # Inject warning as a user-visible note (not system role — Bedrock rejects mid-conversation system messages)
                    warning = f"[Note: Free trial ends in {days} day{'s' if days != 1 else ''}. Please subscribe to continue.]"
                    if warning not in str(self._get_history(chat_id)):
                        self._append(chat_id, "assistant", warning)
                return None  # access allowed

            if status == "expired":
                return (
                    "⏰ *Your free trial has ended.*\n\n"
                    "To continue using the service, please contact us to subscribe.\n\n"
                    "Your data is safe and will be available once you subscribe."
                )

        finally:
            reg_db.close()

        return None  # default allow

    async def handle_image(
        self,
        tenant_id: UUID,
        chat_id: str,
        image_bytes: bytes,
        caption: str,
    ) -> str:
        """
        Process an image message and return the agent's response.

        If the caption clearly identifies the image type (recipe/receipt/order),
        process it immediately. If the caption is missing or unrecognised, store
        the image and ask the user to clarify — never fail silently.

        Args:
            tenant_id: Tenant UUID
            chat_id: Unique conversation identifier
            image_bytes: Raw image bytes
            caption: User-provided caption (may be empty or unrecognised)

        Returns:
            Response string to deliver to the user (never None)
        """
        image_type = self._detect_image_type(caption)

        if image_type is None:
            # Store image bytes so we can process them once the user clarifies
            self._pending_images[chat_id] = image_bytes
            return (
                "📸 Got your image! What type is it?\n\n"
                "Please reply with one of:\n"
                "• *recipe* — handwritten or printed recipe\n"
                "• *receipt* — payment receipt or bill\n"
                "• *order* — WhatsApp/SMS order screenshot\n"
                "• *catalog* — product menu or price list"
            )

        # Clear any pending image for this chat (user sent a new one with a caption)
        self._pending_images.pop(chat_id, None)

        result = await self._extract_image_data(image_type, image_bytes)
        if "error" in result:
            return f"⚠️ {result['error']}"

        summary = self._summarise_image_result(image_type, result)
        logger.info(f"Image summary ({image_type}): {summary[:200]}")

        with _open_db(tenant_id) as db:
            return await self._run_agent(
                db, tenant_id, chat_id,
                user_message=summary,
                history_label=f"[Image: {image_type}]",
            )

    async def close(self) -> None:
        """Shut down the agent and LLM client."""
        await self.agent.close()

    # ── Admin ──────────────────────────────────────────────────────────────

    def _is_admin(self, chat_id: str) -> bool:
        """Return True if this chat_id belongs to the configured admin."""
        return bool(settings.ADMIN_CHAT_ID) and chat_id == settings.ADMIN_CHAT_ID

    async def _handle_admin_message(
        self, tenant_id: UUID, chat_id: str, text: str
    ):
        """Route admin messages: /switch, /delete, /approve, /trial, /status or agent call."""
        if text.startswith("/switch"):
            return self._handle_switch_command(chat_id, text)
        if text.startswith("/delete"):
            return self._handle_delete_command(chat_id, text)
        if text.startswith("/approve"):
            return self._handle_approve_command(chat_id, text)
        if text.startswith("/trial"):
            return self._handle_trial_command(chat_id, text)
        if text.startswith("/status"):
            return self._handle_status_command(chat_id)

        # Resolve which tenant the admin is operating as, then open that tenant's DB.
        # This is the single place where tenant resolution and DB opening are coupled,
        # so there is no risk of mismatch.
        active_tenant_id = self._resolve_admin_tenant(chat_id, tenant_id)
        label = self._admin_label(active_tenant_id)

        with _open_db(active_tenant_id) as db:
            response = await self._run_agent(db, active_tenant_id, chat_id, text)

        # File response (e.g. invoice PDF) — pass through as-is, no label prefix
        if isinstance(response, tuple):
            return response

        return f"_{label}_\n\n{response}"

    def _resolve_admin_tenant(self, admin_chat_id: str, fallback_tenant_id: UUID) -> UUID:
        """
        Determine which tenant the admin should operate as.

        Resolution order:
        1. Already switched this session → use cached target
        2. OWNER_CHAT_ID configured (dedicated deployment) → use that tenant
        3. Scan DB for first non-admin tenant (shared deployment)
        4. Fall back to admin's own tenant
        """
        if admin_chat_id in self._admin_target:
            return self._admin_target[admin_chat_id]

        if settings.OWNER_CHAT_ID:
            from app.services.tenant_service import TenantService
            from app.database import get_registry_db
            reg_db = next(get_registry_db())
            try:
                tenant = TenantService(reg_db).get_or_create_tenant(settings.OWNER_CHAT_ID)
                self._admin_target[admin_chat_id] = tenant.tenant_id
                return tenant.tenant_id
            finally:
                reg_db.close()

        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            first = (
                reg_db.query(Tenant)
                .filter(Tenant.chat_id != admin_chat_id)
                .order_by(Tenant.created_at)
                .first()
            )
            if first:
                self._admin_target[admin_chat_id] = first.tenant_id
                return first.tenant_id
        finally:
            reg_db.close()

        return fallback_tenant_id

    def _admin_label(self, tenant_id: UUID) -> str:
        """Short label shown above every admin response."""
        if settings.OWNER_CHAT_ID:
            return "Admin view"

        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
            if not tenant:
                return "Admin view"
            label = tenant.business_name or tenant.chat_id
            return f"Admin — {label}"
        finally:
            reg_db.close()

    def _handle_switch_command(self, admin_chat_id: str, text: str) -> str:
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            return self._list_tenants(admin_chat_id)

        target_chat_id = parts[1].strip()
        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.chat_id == target_chat_id).first()
            if not tenant:
                return f"❌ No tenant found with chat_id `{target_chat_id}`"
            self._admin_target[admin_chat_id] = tenant.tenant_id
            self._history[admin_chat_id] = []
            return f"✅ Switched to tenant `{target_chat_id}`"
        finally:
            reg_db.close()

    def _list_tenants(self, admin_chat_id: str) -> str:
        """Return a formatted list of all non-admin tenants."""
        from app.models import Tenant
        from app.database import get_registry_db
        reg_db = next(get_registry_db())
        try:
            tenants = (
                reg_db.query(Tenant)
                .filter(Tenant.chat_id != admin_chat_id)
                .order_by(Tenant.created_at)
                .all()
            )
            if not tenants:
                return "No tenants found yet."

            current = self._admin_target.get(admin_chat_id)
            lines = ["*Available tenants:*\n"]
            for t in tenants:
                name = t.business_name or t.chat_id
                marker = " ← current" if current == t.tenant_id else ""
                lines.append(f"• {name} (`{t.chat_id}`){marker}")
            lines.append("\nUse `/switch <chat_id>` to switch.")
            lines.append("Use `/delete <chat_id>` to delete a tenant's data.")
            return "\n".join(lines)
        finally:
            reg_db.close()

    def _handle_delete_command(self, admin_chat_id: str, text: str) -> str:
        """
        Handle /delete <chat_id> command.

        Permanently deletes a tenant's database file and removes them from
        the registry. Used to fulfil user data deletion requests.

        /delete          → show usage
        /delete <id>     → delete that tenant's data
        """
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            return (
                "Usage: `/delete <chat_id>`\n\n"
                "This permanently deletes the tenant's database and removes them "
                "from the registry. Use `/switch` to see available tenants."
            )

        target_chat_id = parts[1].strip()

        from app.models import Tenant
        from app.database import get_registry_db, get_tenant_db_path
        import os

        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.chat_id == target_chat_id).first()
            if not tenant:
                return f"❌ No tenant found with chat_id `{target_chat_id}`"

            name = tenant.business_name or target_chat_id
            tenant_id = tenant.tenant_id

            # Delete the tenant's database file
            db_path = get_tenant_db_path(tenant_id)
            deleted_db = False
            if db_path and os.path.exists(db_path):
                os.remove(db_path)
                # Also remove WAL and SHM files if present
                for ext in ["-wal", "-shm"]:
                    p = db_path + ext
                    if os.path.exists(p):
                        os.remove(p)
                deleted_db = True

            # Remove from registry — use raw SQL on a fresh connection to avoid
            # ORM cascade trying to load related tables that don't exist in tenants.db
            import sqlalchemy as sa
            from app.database import get_registry_db as _get_reg
            fresh_db = next(_get_reg())
            try:
                fresh_db.execute(
                    sa.text("DELETE FROM tenants WHERE tenant_id = :tid"),
                    {"tid": str(tenant_id)}
                )
                fresh_db.commit()
            finally:
                fresh_db.close()

            # Clear any cached admin target pointing to this tenant
            if self._admin_target.get(admin_chat_id) == tenant_id:
                del self._admin_target[admin_chat_id]

            logger.info(f"Admin deleted tenant {target_chat_id} ({name}), db_deleted={deleted_db}")
            return (
                f"✅ Tenant *{name}* (`{target_chat_id}`) has been deleted.\n"
                f"Database file removed: {deleted_db}\n"
                f"Registry entry removed: yes"
            )
        except Exception as e:
            reg_db.rollback()
            logger.error(f"Failed to delete tenant {target_chat_id}: {e}", exc_info=True)
            return f"❌ Failed to delete tenant: {str(e)}"
        finally:
            reg_db.close()

    def _handle_approve_command(self, admin_chat_id: str, text: str) -> str:
        """
        /approve <chat_id> [days]

        Activate a paid subscription for a tenant.
        Default: 30 days. Can extend existing subscription.
        """
        parts = text.strip().split()
        if len(parts) < 2:
            return (
                "Usage: `/approve <chat_id> [days]`\n"
                "Example: `/approve 6834633517 30`\n"
                "Default is 30 days if not specified."
            )

        target_chat_id = parts[1].strip()
        days = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 30

        from app.models import Tenant
        from app.services.tenant_service import TenantService
        from app.database import get_registry_db

        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.chat_id == target_chat_id).first()
            if not tenant:
                return f"❌ No tenant found with chat_id `{target_chat_id}`"
            svc = TenantService(reg_db)
            svc.activate_subscription(tenant.tenant_id, days)
            name = tenant.business_name or target_chat_id
            remaining = svc.days_remaining(tenant.tenant_id)

            # Notify the owner their subscription is active
            owner_msg = (
                f"🎉 Great news, *{name}*!\n\n"
                f"Your subscription is now *active* for {days} days.\n"
                f"You have full access to all features.\n\n"
                + self._capabilities_message()
            )
            import asyncio
            asyncio.create_task(
                self.admin_notifier.send_to_user(target_chat_id, owner_msg)
            )
            asyncio.create_task(
                self.admin_notifier.notify_subscription_event(
                    chat_id=target_chat_id,
                    business_name=name,
                    event="subscription_activated",
                    days=days,
                )
            )

            return (
                f"✅ *{name}* subscription activated!\n"
                f"Days granted: {days}\n"
                f"Total days remaining: {remaining}\n"
                f"Owner has been notified."
            )
        finally:
            reg_db.close()

    def _handle_trial_command(self, admin_chat_id: str, text: str) -> str:
        """
        /trial <chat_id> [days]

        Start or reset a tenant's trial. Default is TRIAL_DAYS (7).
        Admin can override: /trial 6834633517 14
        """
        parts = text.strip().split()
        if len(parts) < 2:
            return "Usage: `/trial <chat_id> [days]`"

        target_chat_id = parts[1].strip()
        custom_days = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None

        from app.models import Tenant
        from app.services.tenant_service import TenantService
        from app.database import get_registry_db

        reg_db = next(get_registry_db())
        try:
            tenant = reg_db.query(Tenant).filter(Tenant.chat_id == target_chat_id).first()
            if not tenant:
                return f"❌ No tenant found with chat_id `{target_chat_id}`"
            svc = TenantService(reg_db)
            if custom_days:
                # Use activate_subscription for custom duration
                svc.activate_subscription(tenant.tenant_id, custom_days)
                trial_days = custom_days
            else:
                svc.start_trial(tenant.tenant_id)
                trial_days = TenantService.TRIAL_DAYS
            remaining = svc.days_remaining(tenant.tenant_id)

            name = tenant.business_name or target_chat_id

            # Notify the owner their trial has started
            owner_msg = (
                f"🎉 Great news, *{name}*!\n\n"
                f"Your *{trial_days}-day free trial* has started. You now have full access.\n\n"
                + self._capabilities_message()
            )
            import asyncio
            asyncio.create_task(
                self.admin_notifier.send_to_user(target_chat_id, owner_msg)
            )
            asyncio.create_task(
                self.admin_notifier.notify_subscription_event(
                    chat_id=target_chat_id,
                    business_name=name,
                    event="trial_started",
                    days=trial_days,
                )
            )

            return f"✅ *{name}* trial started — {remaining} days remaining. Owner has been notified."
        finally:
            reg_db.close()

    def _handle_status_command(self, admin_chat_id: str) -> str:
        """
        /status

        Show all tenants with their subscription status and days remaining.
        """
        from app.models import Tenant
        from app.services.tenant_service import TenantService
        from app.database import get_registry_db

        reg_db = next(get_registry_db())
        try:
            tenants = (
                reg_db.query(Tenant)
                .filter(Tenant.chat_id != admin_chat_id)
                .order_by(Tenant.created_at)
                .all()
            )
            if not tenants:
                return "No tenants found yet."

            svc = TenantService(reg_db)
            lines = ["*Tenant Subscription Status:*\n"]
            status_icons = {
                "pending": "⏳",
                "trial":   "🎁",
                "active":  "✅",
                "expired": "🔴",
            }
            for t in tenants:
                # Auto-update expired status
                status = svc.check_and_update_status(t.tenant_id)
                days = svc.days_remaining(t.tenant_id)
                icon = status_icons.get(status, "❓")
                name = t.business_name or t.chat_id
                days_str = f" ({days}d left)" if days is not None else ""
                lines.append(f"{icon} *{name}* — {status}{days_str}")
                lines.append(f"   `{t.chat_id}`")

            lines.append("\nCommands:")
            lines.append("`/approve <chat_id> [days]` — activate subscription")
            lines.append("`/trial <chat_id>` — reset 7-day trial")
            return "\n".join(lines)
        finally:
            reg_db.close()

    # ── Agent ──────────────────────────────────────────────────────────────

    async def _run_agent(
        self,
        db,
        tenant_id: UUID,
        chat_id: str,
        user_message: str,
        history_label: str = "",
    ) -> str | tuple:
        """
        Run the LLM agent with the current conversation history.

        Returns either:
        - str: a text response to send
        - tuple(bytes, str): PDF bytes and filename to send as a document
        """
        executor = ToolExecutor(db, tenant_id)
        response = await self.agent.run(
            user_message=user_message,
            history=self._get_history(chat_id),
            tool_executor=executor.execute,
        )
        history_entry = f"{history_label} {user_message}".strip() if history_label else user_message
        self._append(chat_id, "user", history_entry)

        # Check if the agent produced an invoice PDF
        if isinstance(response, str) and response.startswith("INVOICE_PDF:"):
            import base64
            parts = response.split(":", 2)
            filename = parts[1]
            pdf_bytes = base64.b64decode(parts[2])
            self._append(chat_id, "assistant", f"[Invoice PDF: {filename}]")
            return pdf_bytes, filename

        # Check if the agent produced an Instagram connect URL
        if isinstance(response, str) and response.startswith("INSTAGRAM_CONNECT_URL:"):
            url = response.split(":", 1)[1]
            if url == "NOT_CONFIGURED":
                msg = (
                    "📱 *Instagram Integration*\n\n"
                    "This feature is not enabled yet. "
                    "We'll notify you as soon as it's available!"
                )
            else:
                msg = (
                    "📱 *Connect your Instagram account*\n\n"
                    "Tap the link below to connect your Instagram. "
                    "Once connected, I'll monitor your DMs and automatically detect orders.\n\n"
                    f"🔗 [Connect Instagram]({url})\n\n"
                    "_The link opens in your browser. After connecting, come back here._"
                )
            self._append(chat_id, "assistant", msg)
            return msg

        self._append(chat_id, "assistant", response)
        return response

    # ── Welcome ────────────────────────────────────────────────────────────

    def _capabilities_message(self) -> str:
        """Capabilities overview shown after onboarding completes."""
        return (
            "Here's what I can help you with — just ask in plain language:\n\n"

            "📦 *Inventory*\n"
            "• Add or update ingredients and packaging\n"
            "• Check stock levels\n"
            "_'Add 5kg flour at ₹40/kg'_\n\n"

            "📖 *Recipes*\n"
            "• Create recipes, calculate cost per unit\n"
            "• Edit or delete recipes\n"
            "_'Create recipe Brownies yield 12'_\n\n"

            "👥 *Customers*\n"
            "• Add and search customers\n"
            "_'Add customer Priya, phone 9876543210'_\n\n"

            "🛒 *Orders*\n"
            "• Create, cancel, or delete orders\n"
            "• Mark delivered, view upcoming or unpaid\n"
            "_'Order for Priya — 2 Brownies at ₹150 each, deliver May 10'_\n\n"

            "💰 *Payments*\n"
            "• Record payments, view history\n"
            "_'Record ₹300 cash payment for Priya'_\n\n"

            "📊 *Reports*\n"
            "• Weekly profit breakdown\n"
            "_'Show this week\\'s profit'_\n\n"

            "📸 *Images*\n"
            "• Send a photo of a recipe, receipt, or order screenshot\n\n"

            "📱 *Instagram*\n"
            "• Say *connect instagram* to auto-detect orders from your DMs\n\n"

            "What would you like to start with?"
        )

    # ── Image processing ───────────────────────────────────────────────────

    # Keywords that identify each image type from the caption
    _IMAGE_TYPE_KEYWORDS: Dict[str, List[str]] = {
        "receipt": ["receipt", "payment", "paid", "bill"],
        "recipe":  ["recipe", "ingredients", "formula"],
        "order":   ["order", "whatsapp", "message", "sms"],
        "catalog": ["catalog", "catalogue", "menu", "price list", "pricelist", "products"],
    }

    def _detect_image_type(self, caption: str) -> Optional[str]:
        """
        Determine image type from caption keywords.

        Returns the image type string, or None if unrecognised.
        """
        caption_lower = caption.lower()
        for image_type, keywords in self._IMAGE_TYPE_KEYWORDS.items():
            if any(kw in caption_lower for kw in keywords):
                return image_type
        return None

    async def _extract_image_data(self, image_type: str, image_bytes: bytes) -> dict:
        """Dispatch to the correct ImageService method based on image type."""
        extractors = {
            "receipt": self.image_service.process_receipt_image,
            "recipe":  self.image_service.process_recipe_image,
            "order":   self.image_service.process_order_image,
            "catalog": self.image_service.process_catalog_image,
        }
        return await extractors[image_type](image_bytes)

    def _summarise_image_result(self, image_type: str, result: dict) -> str:
        """
        Convert extracted image data into a natural language instruction
        that the agent can act on directly.
        """
        summarisers = {
            "receipt": self._summarise_receipt,
            "recipe":  self._summarise_recipe,
            "order":   self._summarise_order,
            "catalog": self._summarise_catalog,
        }
        return summarisers[image_type](result)

    def _summarise_recipe(self, result: dict) -> str:
        """Build agent instruction from extracted recipe data."""
        lines = [
            "I scanned a recipe image. Please create this recipe:",
            f"Name: {result.get('name', 'Unknown')}",
            f"Yield per batch: {result.get('yield_per_batch') or 1}",
        ]
        for ing in result.get("ingredients", []):
            lines.append(f"  Ingredient — {ing.get('item_name')}: {ing.get('quantity')} {ing.get('unit', 'pcs')}")
        for pkg in result.get("packaging", []):
            lines.append(f"  Packaging — {pkg.get('item_name')}: {pkg.get('quantity')} {pkg.get('unit', 'pcs')}")
        lines.append(
            "Create the recipe and add all components. "
            "For any missing inventory items, add them with cost 0 so the user can update later."
        )
        return "\n".join(lines)

    def _summarise_receipt(self, result: dict) -> str:
        """Build agent instruction from extracted receipt data."""
        lines = ["I scanned a payment receipt."]
        if result.get("amount"):
            lines.append(f"Amount: ₹{result['amount']}")
        if result.get("method"):
            lines.append(f"Method: {result['method']}")
        if result.get("customer_name"):
            lines.append(f"Customer: {result['customer_name']}")
        if result.get("date"):
            lines.append(f"Date: {result['date']}")
        lines.append("Please help me record this payment. Ask for any missing details.")
        return "\n".join(lines)

    def _summarise_order(self, result: dict) -> str:
        """Build agent instruction from extracted order data."""
        lines = ["I scanned an order image. Please create this order:"]
        if result.get("customer_name"):
            lines.append(f"Customer: {result['customer_name']}")
        if result.get("customer_phone"):
            lines.append(f"Phone: {result['customer_phone']}")
        if result.get("delivery_date"):
            lines.append(f"Delivery date: {result['delivery_date']}")
        for item in result.get("items", []):
            lines.append(
                f"  - {item.get('recipe_name')}: "
                f"qty {item.get('quantity')}, "
                f"price ₹{item.get('selling_price', 0)}"
            )
        lines.append("Create the order. Ask for any missing details.")
        return "\n".join(lines)

    def _summarise_catalog(self, result: dict) -> str:
        """
        Build agent instruction from extracted catalog data.
        Tells the agent to add all products using add_product tool.
        """
        categories = result.get("categories", [])
        if not categories:
            return "I scanned a catalog image but could not extract any products. Please try again with a clearer image."

        total = sum(len(cat.get("products", [])) for cat in categories)
        lines = [
            f"I scanned a product catalog image. Please add all {total} products to the catalog using the add_product tool.",
            "Here are all the products extracted:\n",
        ]

        for cat in categories:
            cat_name = cat.get("name", "Other")
            lines.append(f"Category: {cat_name}")
            for product in cat.get("products", []):
                name = product.get("name", "")
                variants = product.get("variants", [])
                variant_str = ", ".join(
                    f"{v.get('size_label')} = ₹{v.get('price')}"
                    for v in variants
                )
                lines.append(f"  - {name}: {variant_str}")
            lines.append("")

        lines.append(
            "Add each product with its category and all variants. "
            "After adding all products, tell the owner how many were added and ask if they want to make any changes."
        )
        return "\n".join(lines)

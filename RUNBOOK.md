# KitchenOS Operations Runbook

Quick reference for deploying, monitoring, and recovering the production bot.

**Production instance:** `i-0ff48b32abce4a930` (us-east-1)
**Service name:** `kitchenos`
**App directory:** `/opt/kitchenos`

---

## Adding new columns to existing tables (SQLite)

SQLite uses `Base.metadata.create_all` which only creates new tables — it never
alters existing ones. When a new column is added to a model, run this on the server:

```bash
aws ssm send-command \
  --instance-ids i-0ff48b32abce4a930 \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=[
    "cat > /tmp/migrate.py << '"'"'PYEOF'"'"'",
    "import sqlite3, glob",
    "for db in glob.glob(\"/data/*.db\"):",
    "    if db.endswith((\"-wal\",\"-shm\")): continue",
    "    conn = sqlite3.connect(db)",
    "    cols = [r[1] for r in conn.execute(\"PRAGMA table_info(TABLE_NAME)\").fetchall()]",
    "    if \"NEW_COLUMN\" not in cols:",
    "        conn.execute(\"ALTER TABLE TABLE_NAME ADD COLUMN NEW_COLUMN TYPE DEFAULT VALUE\")",
    "        print(f\"Migrated {db}\")",
    "    conn.commit(); conn.close()",
    "PYEOF",
    "python3.11 /tmp/migrate.py && systemctl restart kitchenos"
  ]' \
  --region us-east-1
```

Replace `TABLE_NAME`, `NEW_COLUMN`, `TYPE`, and `DEFAULT VALUE` as needed.

---

## Connect to the server

```bash
aws ssm start-session --target i-0ff48b32abce4a930 --region us-east-1
```

No SSH key needed — uses AWS SSM Session Manager.

---

## Deploy latest code

### From your laptop (recommended)

Triggers the safe deploy script on the server remotely. You don't need to connect.

```bash
aws ssm send-command \
  --instance-ids i-0ff48b32abce4a930 \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["bash /opt/kitchenos/scripts/deploy.sh"]' \
  --region us-east-1
```

To watch the output:
```bash
# Get the CommandId from the output above, then:
aws ssm get-command-invocation \
  --command-id COMMAND_ID \
  --instance-id i-0ff48b32abce4a930 \
  --region us-east-1 \
  --query '[StandardOutputContent, StandardErrorContent]' \
  --output text
```

### From the server directly

```bash
aws ssm start-session --target i-0ff48b32abce4a930 --region us-east-1
bash /opt/kitchenos/scripts/deploy.sh
```

### What the deploy script does

1. Clones latest code to `/opt/kitchenos-new` — production at `/opt/kitchenos` is untouched
2. Installs any new Python dependencies
3. Copies `.env` from the current deployment (secrets are never in git)
4. **Smoke test** — imports the app; if this fails, deploy aborts, production unchanged
5. **Migration check** — runs Alembic migrations; if this fails, deploy aborts
6. Tags the current commit (e.g. `deploy-20260512-1430`) as a rollback point
7. Swaps `/opt/kitchenos-new` → `/opt/kitchenos` and restarts the service
8. Waits 8 seconds, checks the service is still running
9. **Auto-rollback** — if the health check fails, swaps back to the old version automatically

---

## Manual rollback

The previous version is always saved at `/opt/kitchenos-old` after a deploy.

### Option 1 — Roll back to the previous deploy (fastest)

```bash
aws ssm start-session --target i-0ff48b32abce4a930 --region us-east-1

# On the server:
mv /opt/kitchenos /opt/kitchenos-broken
mv /opt/kitchenos-old /opt/kitchenos
systemctl restart kitchenos
sleep 5
systemctl status kitchenos --no-pager
```

### Option 2 — Roll back to a specific tagged commit

```bash
aws ssm start-session --target i-0ff48b32abce4a930 --region us-east-1

# On the server — list available rollback tags:
git -C /opt/kitchenos tag -l "deploy-*" | sort

# Check out the tag you want:
git -C /opt/kitchenos checkout deploy-20260512-1430
pip3.11 install -q -r /opt/kitchenos/requirements.txt
systemctl restart kitchenos
sleep 5
systemctl status kitchenos --no-pager
```

---

## Check service status

```bash
# Is it running?
aws ssm start-session --target i-0ff48b32abce4a930 --region us-east-1
systemctl status kitchenos --no-pager

# Stream live logs
journalctl -u kitchenos -f

# Last 50 log lines
journalctl -u kitchenos -n 50 --no-pager
```

### Via CloudWatch (from your laptop, no server connection needed)

```bash
# Stream live logs
aws logs tail /kitchenos/prod/app --follow --region us-east-1

# Search for errors in the last hour
aws logs filter-log-events \
  --log-group-name /kitchenos/prod/app \
  --start-time $(date -d '1 hour ago' +%s000) \
  --filter-pattern "ERROR" \
  --region us-east-1
```

---

## Restore from S3 backup

Backups run hourly to S3. Each tenant DB and the registry (`tenants.db`) are backed up.

```bash
# List available backups for a tenant
aws s3 ls s3://kitchenos-prod-db-backups/backups/ --recursive --region us-east-1

# Download a specific backup
aws s3 cp s3://kitchenos-prod-db-backups/backups/<tenant-id>/20260512T120000Z.db /tmp/restore.db

# Restore on the server
aws ssm start-session --target i-0ff48b32abce4a930 --region us-east-1
systemctl stop kitchenos
cp /data/<tenant-id>.db /data/<tenant-id>.db.before-restore
cp /tmp/restore.db /data/<tenant-id>.db
systemctl start kitchenos
```

---

## Alarms and alerts

CloudWatch alarms notify via email when:

| Alarm | Condition |
|---|---|
| CPU high | CPU > 80% for 5 minutes |
| Status check failed | EC2 instance/system check fails |
| Budget 80% | Monthly spend forecast hits 80% of $2 |
| Budget exceeded | Monthly spend exceeds $2 |

The alert email is set at CDK deploy time — see **Setting up alerts** below.

---

## Setting up alerts / CDK deploy

Any time you need to update the CDK stack (alerts, alarms, IAM, etc.), use this command.
Both skip flags are **required** — omitting them causes CloudFormation errors on an existing stack.

```bash
cd infra
./node_modules/.bin/cdk deploy \
  --context deploymentId=prod \
  --context dbEngine=sqlite \
  --context alertEmail=kitchen.ai.os@gmail.com \
  --context monthlyBudgetUsd=2 \
  --context skipVolumeAttachment=true \
  --context skipLogGroupCreation=true
```

| Flag | Why it's needed |
|---|---|
| `skipVolumeAttachment=true` | EBS data volume is already attached — CloudFormation would error or detach it without this |
| `skipLogGroupCreation=true` | CloudWatch log group `/kitchenos/prod/app` already exists — CloudFormation can't recreate it |

AWS will send a confirmation email to `kitchen.ai.os@gmail.com` when the SNS subscription is created.
You must click the link in that email to activate alarm notifications.

> Owner error reports, feedback, and feature requests come via **Telegram** to `ADMIN_CHAT_ID` (`6834633517`). No email needed for those.

---

## AWS free tier status

Everything we use is either free tier eligible or has a permanent free allowance.
The 12-month free tier started when your AWS account was created.

| Service | What we use | Free tier | Cost after free tier |
|---|---|---|---|
| **EC2 t3.micro** | 1 instance, 24/7 | 750 hrs/month for 12 months | ~$8.50/month |
| **EBS gp3** | 28GB total (20GB root + 8GB data) | 30GB/month for 12 months | ~$2.24/month |
| **S3** | ~72 backup files × ~1MB | 5GB storage, 2K PUTs/month for 12 months | Negligible (<$0.01) |
| **CloudWatch Logs** | App logs, low volume | 5GB ingestion + 5GB storage free **always** | $0.50/GB after |
| **CloudWatch Alarms** | 2 alarms (CPU + status check) | 10 alarms free **always** | $0.10/alarm/month |
| **SNS** | Alarm notifications | 1M publishes + 1K email/month free **always** | Negligible |
| **SSM Parameter Store** | ~8 standard parameters | Standard tier free **always** | Free |
| **IAM** | Roles and policies | Always free | Free |
| **Bedrock Nova Lite** | Agent/intent calls | No free tier — pay per token | $0.06/$0.24 per 1M tokens |
| **OpenAI gpt-4o-mini** | Image processing only | No free tier | ~$0.15/$0.60 per 1M tokens |
| **Cloudflare Tunnel** | Webhook URL | Free plan | Free |

**Bottom line during free tier (first 12 months):**
- AWS cost: ~$0 (EC2 + EBS within free limits)
- Bedrock: ~$0.01–0.05/month at low usage (a few hundred messages/day)
- OpenAI: ~$0.01–0.02/month (image processing only, not every message)
- **Total: well under $1/month** — the $2 budget alarm is a safety net

**After free tier expires:**
- EC2 + EBS: ~$10.74/month
- Everything else: same
- **Total: ~$11–12/month** at low usage — raise the budget alarm to $15 at that point

> **Note:** Bedrock has no free tier but Nova Lite is extremely cheap.
> 1,000 messages/day × ~500 tokens each = ~15M tokens/month = ~$3.60/month at full usage.
> At early-stage volumes (50–100 messages/day) it's under $0.20/month.

---

## Emergency: EBS data volume detached

If the data volume gets detached (e.g. after a CDK deploy gone wrong), the bot will
fail to read/write databases. Symptoms: bot starts but all commands error, or service
fails to start with "no such file or directory" on `/data`.

```bash
# 1. Confirm the volume is detached
aws ec2 describe-volumes \
  --volume-ids vol-0a032e059eb0a5e9d \
  --region us-east-1 \
  --query 'Volumes[0].Attachments[0].State' \
  --output text
# Expected: "available" (detached) or missing output

# 2. Reattach it
aws ec2 attach-volume \
  --volume-id vol-0a032e059eb0a5e9d \
  --instance-id i-0ff48b32abce4a930 \
  --device /dev/xvdf \
  --region us-east-1

# 3. Wait for attachment, then remount and restart on the instance
aws ssm send-command \
  --instance-ids i-0ff48b32abce4a930 \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["mount /dev/xvdf /data", "systemctl restart kitchenos", "sleep 5", "systemctl is-active kitchenos"]' \
  --region us-east-1 \
  --query 'Command.CommandId' --output text
```

**Data volume details:**
- Volume ID: `vol-0a032e059eb0a5e9d`
- Device: `/dev/xvdf` → mounted at `/data`
- Contains all tenant SQLite databases

---

## Update secrets (SSM Parameter Store)

```bash
# Update a secret (e.g. rotate the bot token)
aws ssm put-parameter \
  --name /kitchenos/prod/TELEGRAM_BOT_TOKEN \
  --type SecureString \
  --value "NEW_TOKEN" \
  --overwrite \
  --region us-east-1

# After updating secrets, restart the service to pick them up
aws ssm start-session --target i-0ff48b32abce4a930 --region us-east-1
systemctl restart kitchenos
```

---

## Emergency: service won't start at all

```bash
aws ssm start-session --target i-0ff48b32abce4a930 --region us-east-1

# Check what's failing
journalctl -u kitchenos -n 100 --no-pager

# Check the .env is intact
cat /opt/kitchenos/.env

# Try starting manually to see the error directly
cd /opt/kitchenos
python3.11 -m app.telegram_listener
```

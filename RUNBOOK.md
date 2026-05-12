# KitchenOS Operations Runbook

Quick reference for deploying, monitoring, and recovering the production bot.

**Production instance:** `i-0ff48b32abce4a930` (us-east-1)
**Service name:** `kitchenos`
**App directory:** `/opt/kitchenos`

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
| Budget 80% | Monthly spend forecast hits 80% of $20 |
| Budget exceeded | Monthly spend exceeds $20 |

The alert email is set at CDK deploy time — see **Setting up alerts** below.

---

## Setting up alerts (one-time CDK deploy)

Alerts go to whatever email you pass when deploying the CDK stack.
This is separate from the app — it's an AWS-level configuration.

```bash
cd infra
cdk deploy \
  --context deploymentId=prod \
  --context dbEngine=sqlite \
  --context alertEmail=YOUR_EMAIL@example.com \
  --context monthlyBudgetUsd=20 \
  --context skipVolumeAttachment=true
```

> `skipVolumeAttachment=true` is required on all deploys after the first one.
> The EBS volume is already attached to the instance — omitting this flag
> causes CloudFormation to fail with "already attached to an instance".

AWS will send a confirmation email to that address — you must click the link
to activate the subscription before alerts start arriving.

> The app itself (owner error reports, feedback, feature requests) notifies
> you via **Telegram** to your `ADMIN_CHAT_ID` (`6834633517`). No email needed
> for those — they come straight to your Telegram.

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

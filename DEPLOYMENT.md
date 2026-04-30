# Bakery Operations Bot — Deployment Guide

Each bakery gets its own isolated AWS stack: one EC2 instance, one database,
one S3 backup bucket (SQLite path). Stacks are completely independent.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  AWS Stack: BakeryOps-<bakery-id>                   │
│                                                     │
│  ┌──────────────────┐        ┌──────────────────┐  │
│  │  EC2 t4g.nano    │        │  S3 Backup       │  │
│  │  (SQLite path)   │──────▶ │  Bucket          │  │
│  │                  │        │  (hourly backup) │  │
│  │  /data/bakery.db │        └──────────────────┘  │
│  │  (EBS gp3 8GB)   │                              │
│  └──────────────────┘                              │
│                                                     │
│  ┌──────────────────┐        ┌──────────────────┐  │
│  │  EC2 t4g.nano    │        │  RDS             │  │
│  │  (RDS path)      │──────▶ │  db.t4g.micro    │  │
│  │                  │        │  PostgreSQL 16   │  │
│  └──────────────────┘        └──────────────────┘  │
└─────────────────────────────────────────────────────┘
```

## Database options

| | SQLite | RDS PostgreSQL |
|---|---|---|
| **Monthly cost** | ~$5 | ~$21 |
| **Best for** | Single owner, <100 msgs/day | Multiple users, high volume |
| **Backups** | Hourly to S3, 24h retention | Automated by RDS, 3-day retention |
| **Setup complexity** | Simple | Moderate |
| **Scaling** | Upgrade to RDS when needed | Already scalable |

**Rule of thumb**: Start every bakery on SQLite. Migrate to RDS if they add
staff, need concurrent access, or exceed ~500 messages/day.

---

## Monthly cost per bakery

### SQLite path (~$5/month)
| Resource | Cost |
|---|---|
| EC2 t4g.nano (us-east-1) | $3.07 |
| EBS gp3 8GB | $0.64 |
| S3 storage (~50 backups × 1MB) | ~$0.00 |
| LLM API (gpt-4.1-nano) | ~$0.22 |
| **Total** | **~$4/month** |

### RDS path (~$21/month)
| Resource | Cost |
|---|---|
| EC2 t4g.nano | $3.07 |
| RDS db.t4g.micro | $12.10 |
| RDS storage 20GB gp3 | $2.30 |
| LLM API (gpt-4.1-nano) | ~$0.22 |
| **Total** | **~$18/month** |

> Costs are on-demand us-east-1 / ap-south-1 rates. Reserved instances
> (1-year) cut EC2 and RDS costs by ~40%.

---

## Prerequisites

### 1. Install tools

```bash
# Node.js 20+
node --version

# AWS CDK
npm install -g aws-cdk

# AWS CLI configured with your account
aws configure

# Bootstrap CDK in your account (one-time per account/region)
cdk bootstrap aws://ACCOUNT_ID/ap-south-1
```

### 2. Install infra dependencies

```bash
cd infra
npm install
```

---

## Deploying a new bakery

### Step 1 — Store secrets in SSM Parameter Store

```bash
BAKERY_ID="priya-bakery"   # unique slug for this bakery

aws ssm put-parameter \
  --name "/bakery/${BAKERY_ID}/TELEGRAM_BOT_TOKEN" \
  --type SecureString \
  --value "YOUR_TELEGRAM_BOT_TOKEN"

aws ssm put-parameter \
  --name "/bakery/${BAKERY_ID}/LLM_API_KEY" \
  --type SecureString \
  --value "YOUR_OPENAI_API_KEY"
```

### Step 2 — Deploy the stack

**SQLite (small bakery):**
```bash
cd infra
cdk deploy \
  --context bakeryId=priya-bakery \
  --context dbEngine=sqlite
```

**RDS (larger bakery):**
```bash
cd infra
cdk deploy \
  --context bakeryId=raj-bakery \
  --context dbEngine=rds
```

CDK will print the EC2 instance ID when complete.

### Step 3 — Verify the bot is running

```bash
# Connect via SSM Session Manager (no SSH key needed)
aws ssm start-session --target INSTANCE_ID

# On the instance:
systemctl status bakery-bot
journalctl -u bakery-bot -f
```

---

## Backup and recovery (SQLite)

### How backups work

The bot runs a background task that:
1. Every hour: creates a consistent SQLite snapshot using the built-in
   online backup API (safe while the DB is in use)
2. Uploads the snapshot to S3 as `backups/YYYYMMDDTHHMMSSZ.db`
3. Deletes backups older than 24 hours

The S3 bucket also has a 3-day lifecycle rule as a safety net.

### Restore from backup

```bash
# List available backups
aws s3 ls s3://bakery-priya-bakery-db-backups/backups/

# Download the most recent backup
aws s3 cp s3://bakery-priya-bakery-db-backups/backups/20260430T120000Z.db /tmp/restore.db

# On the EC2 instance (via SSM):
systemctl stop bakery-bot
cp /data/bakery.db /data/bakery.db.before-restore
cp /tmp/restore.db /data/bakery.db
systemctl start bakery-bot
```

### Manual backup trigger

```bash
# On the EC2 instance:
cd /opt/bakery
python3.11 -c "
import asyncio
from app.services.backup_service import create_backup_service
svc = create_backup_service()
key = asyncio.run(svc.backup_now())
print(f'Backed up to: {key}')
"
```

---

## Updating the bot

```bash
# Connect to the instance
aws ssm start-session --target INSTANCE_ID

# On the instance:
cd /opt/bakery
git pull
pip3.11 install -r requirements.txt
python3.11 -m alembic upgrade head   # run any new migrations
systemctl restart bakery-bot
```

---

## Migrating SQLite → RDS

When a bakery grows and needs RDS:

### 1. Export SQLite data

```bash
# On the EC2 instance:
systemctl stop bakery-bot
cd /opt/bakery
python3.11 -c "
import sqlite3, json
conn = sqlite3.connect('/data/bakery.db')
conn.row_factory = sqlite3.Row
# Export all tables to JSON for import
tables = conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
for t in tables:
    rows = conn.execute(f'SELECT * FROM {t[0]}').fetchall()
    with open(f'/tmp/{t[0]}.json', 'w') as f:
        json.dump([dict(r) for r in rows], f)
    print(f'Exported {len(rows)} rows from {t[0]}')
"
```

### 2. Deploy new RDS stack

```bash
# Destroy old SQLite stack (keeps EBS and S3 due to RETAIN policy)
cdk destroy --context bakeryId=priya-bakery --context dbEngine=sqlite

# Deploy new RDS stack with same bakeryId
cdk deploy --context bakeryId=priya-bakery --context dbEngine=rds
```

### 3. Import data

```bash
# Run migrations on new RDS instance
python3.11 -m alembic upgrade head

# Import data (use your preferred method — psql COPY, SQLAlchemy bulk insert, etc.)
```

---

## Destroying a stack

```bash
# This will NOT delete the EBS volume or S3 backup bucket (RemovalPolicy.RETAIN)
cdk destroy --context bakeryId=priya-bakery --context dbEngine=sqlite

# To fully clean up (after confirming data is no longer needed):
aws s3 rb s3://bakery-priya-bakery-db-backups --force
aws ec2 delete-volume --volume-id vol-XXXXXXXXX
```

---

## Monitoring

The bot logs to systemd journal. To stream logs:

```bash
aws ssm start-session --target INSTANCE_ID
journalctl -u bakery-bot -f
```

For production, consider shipping logs to CloudWatch Logs by adding the
CloudWatch agent to the user-data script. Cost: ~$0.50/month per bakery.

---

## Security notes

- No inbound ports open on the EC2 instance — bot uses outbound Telegram polling
- SSH access via SSM Session Manager only (no key pairs, no port 22)
- Secrets stored in SSM Parameter Store (SecureString, KMS encrypted)
- RDS not publicly accessible, isolated subnet
- EBS volume encrypted at rest
- S3 bucket blocks all public access

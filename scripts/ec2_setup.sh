#!/bin/bash
set -e

REGION=us-east-1
SSM_PREFIX=/kitchenos/prod

echo "=== Fetching secrets from SSM ==="
GITHUB_TOKEN=$(aws ssm get-parameter --name $SSM_PREFIX/GITHUB_TOKEN --with-decryption --query Parameter.Value --output text --region $REGION)
TELEGRAM_BOT_TOKEN=$(aws ssm get-parameter --name $SSM_PREFIX/TELEGRAM_BOT_TOKEN --with-decryption --query Parameter.Value --output text --region $REGION)
LLM_API_KEY=$(aws ssm get-parameter --name $SSM_PREFIX/LLM_API_KEY --with-decryption --query Parameter.Value --output text --region $REGION)
ADMIN_CHAT_ID=$(aws ssm get-parameter --name $SSM_PREFIX/ADMIN_CHAT_ID --query Parameter.Value --output text --region $REGION)
META_APP_ID=$(aws ssm get-parameter --name $SSM_PREFIX/META_APP_ID --query Parameter.Value --output text --region $REGION 2>/dev/null || echo "")
META_APP_SECRET=$(aws ssm get-parameter --name $SSM_PREFIX/META_APP_SECRET --with-decryption --query Parameter.Value --output text --region $REGION 2>/dev/null || echo "")
INSTAGRAM_VERIFY_TOKEN=$(aws ssm get-parameter --name $SSM_PREFIX/INSTAGRAM_VERIFY_TOKEN --query Parameter.Value --output text --region $REGION 2>/dev/null || echo "")

echo "=== Cloning repository ==="
rm -rf /opt/kitchenos
git clone https://$GITHUB_TOKEN@github.com/ahujahaymang/bakery-manager /opt/kitchenos

echo "=== Installing dependencies ==="
pip3.11 install -r /opt/kitchenos/requirements.txt

echo "=== Writing .env ==="
cat > /opt/kitchenos/.env << ENVEOF
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
LLM_API_KEY=$LLM_API_KEY
LLM_MODEL=gpt-4o-mini
BEDROCK_MODEL=amazon.nova-lite-v1:0
AWS_REGION=$REGION
ADMIN_CHAT_ID=$ADMIN_CHAT_ID
DB_ENGINE=sqlite
SQLITE_PATH=/data
S3_BACKUP_BUCKET=kitchenos-prod-db-backups
S3_BACKUP_PREFIX=backups
S3_BACKUP_RETAIN_HOURS=24
META_APP_ID=$META_APP_ID
META_APP_SECRET=$META_APP_SECRET
INSTAGRAM_VERIFY_TOKEN=$INSTAGRAM_VERIFY_TOKEN
ENVEOF

echo "=== Creating systemd service ==="
cat > /etc/systemd/system/kitchenos.service << SVCEOF
[Unit]
Description=KitchenOS Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/kitchenos
ExecStart=/usr/bin/python3.11 -m app.telegram_listener
Restart=always
RestartSec=10
EnvironmentFile=/opt/kitchenos/.env

[Install]
WantedBy=multi-user.target
SVCEOF

echo "=== Starting service ==="
systemctl daemon-reload
systemctl enable kitchenos
systemctl start kitchenos
sleep 5
systemctl status kitchenos --no-pager
echo "=== Setup complete ==="

#!/usr/bin/env node
/**
 * KitchenOS CDK App
 *
 * Deploy a KitchenOS server to AWS.
 *
 * Usage:
 *   # Shared server with SQLite (recommended — free tier eligible)
 *   cdk deploy --context deploymentId=prod --context dbEngine=sqlite
 *
 *   # With RDS PostgreSQL (for high-volume deployments)
 *   cdk deploy --context deploymentId=prod --context dbEngine=rds
 *
 * Required context:
 *   deploymentId  — unique name for this deployment, e.g. "prod", "staging"
 *   dbEngine      — "sqlite" (default) or "rds"
 *
 * Before deploying, store secrets in SSM Parameter Store:
 *   aws ssm put-parameter --name /kitchenos/<deploymentId>/TELEGRAM_BOT_TOKEN --type SecureString --value <token>
 *   aws ssm put-parameter --name /kitchenos/<deploymentId>/LLM_API_KEY --type SecureString --value <key>
 *   aws ssm put-parameter --name /kitchenos/<deploymentId>/ADMIN_CHAT_ID --type String --value <chat_id>
 */

import * as cdk from 'aws-cdk-lib';
import { KitchenOsStack } from '../lib/kitchenos-stack';

const app = new cdk.App();

const deploymentId = app.node.tryGetContext('deploymentId');
const dbEngine = app.node.tryGetContext('dbEngine') ?? 'sqlite';

if (deploymentId) {
  if (!['sqlite', 'rds'].includes(dbEngine)) {
    throw new Error('dbEngine must be "sqlite" or "rds"');
  }

  new KitchenOsStack(app, `KitchenOS-${deploymentId}`, {
    deploymentId,
    dbEngine: dbEngine as 'sqlite' | 'rds',
    env: {
      account: process.env.CDK_DEFAULT_ACCOUNT,
      region: process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
    },
    description: `KitchenOS Bot — ${deploymentId} (${dbEngine})`,
  });
}

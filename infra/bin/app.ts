#!/usr/bin/env node
/**
 * CDK App entry point.
 *
 * Deploy one stack per bakery:
 *
 *   # SQLite (small bakery) — cheapest
 *   cdk deploy --context bakeryId=priya-bakery --context dbEngine=sqlite
 *
 *   # RDS PostgreSQL (larger bakery)
 *   cdk deploy --context bakeryId=raj-bakery --context dbEngine=rds
 *
 * Required context:
 *   bakeryId   - unique slug, used in all resource names (e.g. "priya-bakery")
 *   dbEngine   - "sqlite" | "rds"
 *
 * Required environment variables (set in AWS SSM or passed via --context):
 *   TELEGRAM_BOT_TOKEN
 *   LLM_API_KEY
 */

import * as cdk from 'aws-cdk-lib';
import { BakeryStack } from '../lib/bakery-stack';

const app = new cdk.App();

const bakeryId = app.node.tryGetContext('bakeryId');
const dbEngine  = app.node.tryGetContext('dbEngine') ?? 'sqlite';

if (!bakeryId) {
  throw new Error('Missing required context: --context bakeryId=<slug>');
}
if (!['sqlite', 'rds'].includes(dbEngine)) {
  throw new Error('dbEngine must be "sqlite" or "rds"');
}

new BakeryStack(app, `BakeryOps-${bakeryId}`, {
  bakeryId,
  dbEngine: dbEngine as 'sqlite' | 'rds',
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region:  process.env.CDK_DEFAULT_REGION ?? 'ap-south-1',
  },
  description: `Bakery Operations Bot — ${bakeryId} (${dbEngine})`,
});

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatchActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import { Construct } from 'constructs';

export interface KitchenOsStackProps extends cdk.StackProps {
  /**
   * Unique deployment identifier, e.g. "prod", "staging", "shared".
   * Used to name all AWS resources. Must be lowercase alphanumeric + hyphens.
   */
  deploymentId: string;

  /**
   * Database engine:
   *   "sqlite" — per-tenant SQLite files on EBS (cheap, shared server)
   *   "rds"    — PostgreSQL on RDS (high-volume or compliance)
   */
  dbEngine: 'sqlite' | 'rds';

  /**
   * Email address to receive CloudWatch alarms and budget alerts.
   * If omitted, alarms are created but no email subscription is added.
   */
  alertEmail?: string;

  /**
   * Monthly budget threshold in USD. Alarm fires when forecast exceeds this.
   * Default: 20 (USD).
   */
  monthlyBudgetUsd?: number;
}

/**
 * KitchenOS deployment stack.
 *
 * One stack = one deployment (typically one shared server serving all tenants).
 *
 * SQLite path (~$5/month on free tier, $10/month after):
 *   t3.micro EC2 + 8GB EBS (encrypted) + S3 backup bucket
 *
 * RDS path (~$21/month):
 *   t3.micro EC2 + db.t3.micro PostgreSQL
 *
 * Deploy:
 *   cdk deploy --context deploymentId=prod --context dbEngine=sqlite
 */
export class KitchenOsStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: KitchenOsStackProps) {
    super(scope, id, props);

    const { deploymentId, dbEngine, alertEmail, monthlyBudgetUsd = 20 } = props;
    const slug = deploymentId.toLowerCase().replace(/[^a-z0-9-]/g, '-');
    const prefix = `kitchenos-${slug}`;

    // ── VPC ──────────────────────────────────────────────────────────────
    // Single public subnet — no NAT gateway needed, saves ~$32/month.
    const vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: `${prefix}-vpc`,
      maxAzs: 1,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 28,
        },
        ...(dbEngine === 'rds' ? [{
          name: 'private',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 28,
        }] : []),
      ],
    });

    // ── Security group ────────────────────────────────────────────────────
    // No inbound rules — bot uses outbound polling, no public ports needed.
    // Port 8000 (webhook server) only needs to be open if you have a public domain.
    const appSg = new ec2.SecurityGroup(this, 'AppSg', {
      vpc,
      securityGroupName: `${prefix}-app-sg`,
      description: 'KitchenOS app - outbound only',
      allowAllOutbound: true,
    });

    // ── IAM role for EC2 ─────────────────────────────────────────────────
    const role = new iam.Role(this, 'Ec2Role', {
      roleName: `${prefix}-ec2-role`,
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        // SSM Session Manager — connect without opening port 22
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });

    // Allow EC2 to call Amazon Bedrock (Nova Lite for agent loop)
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['arn:aws:bedrock:*::foundation-model/amazon.nova-*'],
    }));

    // ── SSM Parameters ────────────────────────────────────────────────────
    // Secrets are stored in SSM Parameter Store. Create before deploying:
    //   aws ssm put-parameter --name /kitchenos/<id>/TELEGRAM_BOT_TOKEN --type SecureString --value <token>
    //   aws ssm put-parameter --name /kitchenos/<id>/LLM_API_KEY --type SecureString --value <key>
    //   aws ssm put-parameter --name /kitchenos/<id>/ADMIN_CHAT_ID --type String --value <chat_id>
    const ssmPrefix = `/kitchenos/${slug}`;

    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:GetParameters'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter${ssmPrefix}/*`,
      ],
    }));

    // ── S3 backup bucket (SQLite only) ────────────────────────────────────
    let backupBucket: s3.Bucket | undefined;
    let backupEnv: Record<string, string> = {};

    if (dbEngine === 'sqlite') {
      backupBucket = new s3.Bucket(this, 'BackupBucket', {
        bucketName: `${prefix}-db-backups`,
        versioned: false,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        encryption: s3.BucketEncryption.S3_MANAGED,
        lifecycleRules: [{
          id: 'expire-old-backups',
          expiration: cdk.Duration.days(3),
        }],
        removalPolicy: cdk.RemovalPolicy.RETAIN,
      });

      backupBucket.grantReadWrite(role);

      backupEnv = {
        DB_ENGINE: 'sqlite',
        SQLITE_PATH: '/data',
        S3_BACKUP_BUCKET: backupBucket.bucketName,
        S3_BACKUP_PREFIX: 'backups',
        S3_BACKUP_RETAIN_HOURS: '24',
      };
    }

    // ── RDS (PostgreSQL) ──────────────────────────────────────────────────
    let rdsEnv: Record<string, string> = {};
    let dbInstance: rds.DatabaseInstance | undefined;

    if (dbEngine === 'rds') {
      const dbSg = new ec2.SecurityGroup(this, 'DbSg', {
        vpc,
        securityGroupName: `${prefix}-db-sg`,
        description: 'RDS - allow from app only',
        allowAllOutbound: false,
      });
      dbSg.addIngressRule(appSg, ec2.Port.tcp(5432), 'Allow from app');

      const dbSecret = new rds.DatabaseSecret(this, 'DbSecret', {
        secretName: `${prefix}-db-credentials`,
        username: 'kitchenos',
      });

      dbInstance = new rds.DatabaseInstance(this, 'Db', {
        engine: rds.DatabaseInstanceEngine.postgres({
          version: rds.PostgresEngineVersion.VER_16,
        }),
        instanceType: ec2.InstanceType.of(
          ec2.InstanceClass.T3,
          ec2.InstanceSize.MICRO,
        ),
        vpc,
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
        securityGroups: [dbSg],
        credentials: rds.Credentials.fromSecret(dbSecret),
        databaseName: 'kitchenos',
        allocatedStorage: 20,
        storageType: rds.StorageType.GP3,
        backupRetention: cdk.Duration.days(3),
        deletionProtection: true,
        multiAz: false,
        publiclyAccessible: false,
        instanceIdentifier: `${prefix}-db`,
      });

      dbSecret.grantRead(role);

      rdsEnv = {
        DB_ENGINE: 'postgresql',
        DB_HOST: dbInstance.dbInstanceEndpointAddress,
        DB_PORT: '5432',
        DB_NAME: 'kitchenos',
        DB_USER: 'kitchenos',
      };
    }

    // ── EC2 user-data ─────────────────────────────────────────────────────
    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      'set -e',
      'yum update -y',
      'yum install -y python3.11 python3.11-pip git',

      // Install and configure CloudWatch agent for application logs
      'yum install -y amazon-cloudwatch-agent',
      `cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'CWEOF'`,
      '{',
      '  "logs": {',
      '    "logs_collected": {',
      '      "files": {',
      '        "collect_list": [',
      '          {',
      `            "file_path": "/var/log/kitchenos/app.log",`,
      `            "log_group_name": "/kitchenos/${slug}/app",`,
      '            "log_stream_name": "{instance_id}",',
      '            "timestamp_format": "%Y-%m-%d %H:%M:%S"',
      '          }',
      '        ]',
      '      }',
      '    }',
      '  }',
      '}',
      'CWEOF',
      '/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json',
      'mkdir -p /var/log/kitchenos',

      // Mount EBS data volume for SQLite files
      ...(dbEngine === 'sqlite' ? [
        'mkdir -p /data',
        'if ! blkid /dev/xvdf; then mkfs.ext4 /dev/xvdf; fi',
        'mount /dev/xvdf /data',
        'echo "/dev/xvdf /data ext4 defaults,nofail 0 2" >> /etc/fstab',
      ] : []),

      `GITHUB_TOKEN=$(aws ssm get-parameter --name ${ssmPrefix}/GITHUB_TOKEN --with-decryption --query Parameter.Value --output text 2>/dev/null || echo "")`,

      // Clone app from GitHub
      'mkdir -p /opt/kitchenos',
      'if [ -n "$GITHUB_TOKEN" ]; then',
      '  git clone https://$GITHUB_TOKEN@github.com/ahujahaymang/bakery-manager /opt/kitchenos || (cd /opt/kitchenos && git pull)',
      'else',
      '  git clone https://github.com/ahujahaymang/bakery-manager /opt/kitchenos || (cd /opt/kitchenos && git pull)',
      'fi',
      'cd /opt/kitchenos && pip3.11 install -r requirements.txt',

      // Fetch secrets from SSM Parameter Store
      `TELEGRAM_BOT_TOKEN=$(aws ssm get-parameter --name ${ssmPrefix}/TELEGRAM_BOT_TOKEN --with-decryption --query Parameter.Value --output text)`,
      `LLM_API_KEY=$(aws ssm get-parameter --name ${ssmPrefix}/LLM_API_KEY --with-decryption --query Parameter.Value --output text)`,
      `ADMIN_CHAT_ID=$(aws ssm get-parameter --name ${ssmPrefix}/ADMIN_CHAT_ID --query Parameter.Value --output text 2>/dev/null || echo "")`,
      `OWNER_CHAT_ID=$(aws ssm get-parameter --name ${ssmPrefix}/OWNER_CHAT_ID --query Parameter.Value --output text 2>/dev/null || echo "")`,
      `META_APP_ID=$(aws ssm get-parameter --name ${ssmPrefix}/META_APP_ID --query Parameter.Value --output text 2>/dev/null || echo "")`,
      `META_APP_SECRET=$(aws ssm get-parameter --name ${ssmPrefix}/META_APP_SECRET --with-decryption --query Parameter.Value --output text 2>/dev/null || echo "")`,
      `INSTAGRAM_VERIFY_TOKEN=$(aws ssm get-parameter --name ${ssmPrefix}/INSTAGRAM_VERIFY_TOKEN --query Parameter.Value --output text 2>/dev/null || echo "")`,

      ...(dbEngine === 'rds' ? [
        `DB_PASSWORD=$(aws secretsmanager get-secret-value --secret-id ${prefix}-db-credentials --query SecretString --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")`,
      ] : []),

      // Write .env
      'cat > /opt/kitchenos/.env << EOF',
      'TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN',
      'LLM_API_KEY=$LLM_API_KEY',
      'LLM_MODEL=gpt-4o-mini',
      'BEDROCK_MODEL=amazon.nova-lite-v1:0',
      `AWS_REGION=${this.region}`,
      'ADMIN_CHAT_ID=$ADMIN_CHAT_ID',
      'OWNER_CHAT_ID=$OWNER_CHAT_ID',
      'META_APP_ID=$META_APP_ID',
      'META_APP_SECRET=$META_APP_SECRET',
      'INSTAGRAM_VERIFY_TOKEN=$INSTAGRAM_VERIFY_TOKEN',
      ...Object.entries({ ...backupEnv, ...rdsEnv }).map(([k, v]) => `${k}=${v}`),
      ...(dbEngine === 'rds' ? ['DB_PASSWORD=$DB_PASSWORD'] : []),
      'EOF',

      // Run migrations (SQLite: auto-created; RDS: run alembic)
      ...(dbEngine === 'rds' ? [
        'cd /opt/kitchenos && python3.11 -m alembic upgrade head',
      ] : []),

      // Create systemd service
      'cat > /etc/systemd/system/kitchenos.service << EOF',
      '[Unit]',
      'Description=KitchenOS Bot',
      'After=network.target',
      '',
      '[Service]',
      'Type=simple',
      'User=root',
      'WorkingDirectory=/opt/kitchenos',
      'ExecStart=/usr/bin/python3.11 -m app.telegram_listener',
      'Restart=always',
      'RestartSec=10',
      'EnvironmentFile=/opt/kitchenos/.env',
      'StandardOutput=append:/var/log/kitchenos/app.log',
      'StandardError=append:/var/log/kitchenos/app.log',
      '',
      '[Install]',
      'WantedBy=multi-user.target',
      'EOF',

      'systemctl daemon-reload',
      'systemctl enable kitchenos',
      'systemctl start kitchenos',
    );

    // ── EC2 instance (t3.micro — free tier eligible) ──────────────────────
    const instance = new ec2.Instance(this, 'App', {
      instanceName: `${prefix}-app`,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      instanceType: ec2.InstanceType.of(
        ec2.InstanceClass.T3,
        ec2.InstanceSize.MICRO,
      ),
      machineImage: ec2.MachineImage.latestAmazonLinux2023({
        cpuType: ec2.AmazonLinuxCpuType.X86_64,
      }),
      securityGroup: appSg,
      role,
      userData,
      disableApiTermination: true,
      blockDevices: [{
        deviceName: '/dev/xvda',
        volume: ec2.BlockDeviceVolume.ebs(20, {
          volumeType: ec2.EbsDeviceVolumeType.GP3,
          encrypted: true,
        }),
      }],
    });

    // ── EBS data volume for SQLite ────────────────────────────────────────
    if (dbEngine === 'sqlite') {
      const dataVolume = new ec2.Volume(this, 'DataVolume', {
        volumeName: `${prefix}-data`,
        availabilityZone: instance.instanceAvailabilityZone,
        size: cdk.Size.gibibytes(8),
        volumeType: ec2.EbsDeviceVolumeType.GP3,
        encrypted: true,
        removalPolicy: cdk.RemovalPolicy.RETAIN,
      });

      // Only create the attachment on first deploy.
      // On subsequent deploys the volume is already attached — skip to avoid
      // the "already attached to an instance" CloudFormation error.
      const skipAttachment = this.node.tryGetContext('skipVolumeAttachment') === 'true';
      if (!skipAttachment) {
        const attachment = new ec2.CfnVolumeAttachment(this, 'DataVolumeAttachment', {
          instanceId: instance.instanceId,
          volumeId: dataVolume.volumeId,
          device: '/dev/xvdf',
        });
        // Retain on delete — never detach the data volume automatically
        attachment.cfnOptions.deletionPolicy = cdk.CfnDeletionPolicy.RETAIN;
      }
    }

    // ── Monitoring & Alarms ───────────────────────────────────────────────

    // CloudWatch Log Group — app writes here via the CloudWatch agent
    const logGroup = new logs.LogGroup(this, 'AppLogGroup', {
      logGroupName: `/kitchenos/${slug}/app`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Allow EC2 to write logs to CloudWatch
    role.addToPolicy(new iam.PolicyStatement({
      actions: [
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
        'logs:DescribeLogStreams',
      ],
      resources: [logGroup.logGroupArn, `${logGroup.logGroupArn}:*`],
    }));

    // SNS topic for all alarms
    const alarmTopic = new sns.Topic(this, 'AlarmTopic', {
      topicName: `${prefix}-alarms`,
      displayName: `KitchenOS ${deploymentId} Alarms`,
    });

    if (alertEmail) {
      alarmTopic.addSubscription(
        new snsSubscriptions.EmailSubscription(alertEmail)
      );
    }

    // EC2 CPU utilisation alarm — fires when CPU > 80% for 5 minutes
    new cloudwatch.Alarm(this, 'CpuAlarm', {
      alarmName: `${prefix}-cpu-high`,
      alarmDescription: 'EC2 CPU utilisation above 80% for 5 minutes',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/EC2',
        metricName: 'CPUUtilization',
        dimensionsMap: { InstanceId: instance.instanceId },
        period: cdk.Duration.minutes(5),
        statistic: 'Average',
      }),
      threshold: 80,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    // EC2 status check alarm — fires when instance fails system/instance checks
    new cloudwatch.Alarm(this, 'StatusCheckAlarm', {
      alarmName: `${prefix}-status-check-failed`,
      alarmDescription: 'EC2 instance or system status check failed',
      metric: new cloudwatch.Metric({
        namespace: 'AWS/EC2',
        metricName: 'StatusCheckFailed',
        dimensionsMap: { InstanceId: instance.instanceId },
        period: cdk.Duration.minutes(5),
        statistic: 'Maximum',
      }),
      threshold: 1,
      evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }).addAlarmAction(new cloudwatchActions.SnsAction(alarmTopic));

    // AWS Budget alarm — fires when monthly forecast exceeds threshold
    new budgets.CfnBudget(this, 'MonthlyBudget', {
      budget: {
        budgetName: `${prefix}-monthly-budget`,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: {
          amount: monthlyBudgetUsd,
          unit: 'USD',
        },
      },
      notificationsWithSubscribers: alertEmail ? [
        {
          notification: {
            notificationType: 'FORECASTED',
            comparisonOperator: 'GREATER_THAN',
            threshold: 80,  // alert at 80% of budget
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            { subscriptionType: 'EMAIL', address: alertEmail },
          ],
        },
        {
          notification: {
            notificationType: 'ACTUAL',
            comparisonOperator: 'GREATER_THAN',
            threshold: 100,  // alert when budget is exceeded
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [
            { subscriptionType: 'EMAIL', address: alertEmail },
          ],
        },
      ] : [],
    });

    // ── Outputs ───────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'InstanceId', {
      value: instance.instanceId,
      description: 'EC2 instance ID — connect via: aws ssm start-session --target <id>',
    });

    new cdk.CfnOutput(this, 'InstancePublicIp', {
      value: instance.instancePublicIp,
      description: 'Public IP (for webhook server / ngrok alternative)',
    });

    new cdk.CfnOutput(this, 'LogGroupName', {
      value: logGroup.logGroupName,
      description: 'CloudWatch Log Group — view app logs in the AWS console',
    });

    new cdk.CfnOutput(this, 'AlarmTopicArn', {
      value: alarmTopic.topicArn,
      description: 'SNS topic ARN for CloudWatch alarms',
    });

    if (backupBucket) {
      new cdk.CfnOutput(this, 'BackupBucketName', {
        value: backupBucket.bucketName,
        description: 'S3 bucket for SQLite backups',
      });
    }

    if (dbInstance) {
      new cdk.CfnOutput(this, 'DbEndpoint', {
        value: dbInstance.dbInstanceEndpointAddress,
        description: 'RDS endpoint',
      });
    }
  }
}

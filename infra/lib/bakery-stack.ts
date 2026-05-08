import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as rds from 'aws-cdk-lib/aws-rds';
import { Construct } from 'constructs';

export interface BakeryStackProps extends cdk.StackProps {
  /** Unique slug for this bakery, e.g. "priya-bakery" */
  bakeryId: string;
  /** "sqlite" for small bakeries, "rds" for larger ones */
  dbEngine: 'sqlite' | 'rds';
}

/**
 * One stack = one bakery.
 *
 * SQLite path  → t4g.nano EC2 + EBS volume + S3 backup bucket  (~$5/month)
 * RDS path     → t4g.micro EC2 + db.t4g.micro RDS              (~$21/month)
 */
export class BakeryStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: BakeryStackProps) {
    super(scope, id, props);

    const { bakeryId, dbEngine } = props;
    const slug = bakeryId.toLowerCase().replace(/[^a-z0-9-]/g, '-');

    // ── VPC ──────────────────────────────────────────────────────────────
    // Single public subnet — no NAT gateway needed, saves ~$32/month.
    const vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: `bakery-${slug}-vpc`,
      maxAzs: 1,
      natGateways: 0,
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 28,
        },
        // Private subnet only created for RDS path
        ...(dbEngine === 'rds' ? [{
          name: 'private',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 28,
        }] : []),
      ],
    });

    // ── Security group ────────────────────────────────────────────────────
    const appSg = new ec2.SecurityGroup(this, 'AppSg', {
      vpc,
      securityGroupName: `bakery-${slug}-app-sg`,
      description: 'Bakery bot — outbound only (Telegram polling)',
      allowAllOutbound: true,
    });
    // No inbound rules — bot uses outbound polling, no public ports needed.

    // ── IAM role for EC2 ─────────────────────────────────────────────────
    const role = new iam.Role(this, 'Ec2Role', {
      roleName: `bakery-${slug}-ec2-role`,
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        // SSM Session Manager — SSH without opening port 22
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });

    // ── S3 backup bucket (SQLite only) ────────────────────────────────────
    let backupBucket: s3.Bucket | undefined;
    let backupEnv: Record<string, string> = {};

    if (dbEngine === 'sqlite') {
      backupBucket = new s3.Bucket(this, 'BackupBucket', {
        bucketName: `bakery-${slug}-db-backups`,
        versioned: false,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        encryption: s3.BucketEncryption.S3_MANAGED,
        // Auto-delete objects after 3 days as a safety net
        lifecycleRules: [{
          id: 'expire-old-backups',
          expiration: cdk.Duration.days(3),
        }],
        removalPolicy: cdk.RemovalPolicy.RETAIN, // keep backups if stack is deleted
      });

      backupBucket.grantReadWrite(role);

      backupEnv = {
        DB_ENGINE: 'sqlite',
        SQLITE_PATH: '/data/bakery.db',
        S3_BACKUP_BUCKET: backupBucket.bucketName,
        S3_BACKUP_PREFIX: 'backups',
        S3_BACKUP_RETAIN_HOURS: '24',
      };
    }

    // ── RDS (PostgreSQL, only for rds engine) ─────────────────────────────
    let rdsEnv: Record<string, string> = {};
    let dbInstance: rds.DatabaseInstance | undefined;

    if (dbEngine === 'rds') {
      const dbSg = new ec2.SecurityGroup(this, 'DbSg', {
        vpc,
        securityGroupName: `bakery-${slug}-db-sg`,
        description: 'RDS — allow from app only',
        allowAllOutbound: false,
      });
      dbSg.addIngressRule(appSg, ec2.Port.tcp(5432), 'Allow from app');

      const dbSecret = new rds.DatabaseSecret(this, 'DbSecret', {
        secretName: `bakery-${slug}-db-credentials`,
        username: 'bakery',
      });

      dbInstance = new rds.DatabaseInstance(this, 'Db', {
        engine: rds.DatabaseInstanceEngine.postgres({
          version: rds.PostgresEngineVersion.VER_16,
        }),
        instanceType: ec2.InstanceType.of(
          ec2.InstanceClass.T4G,
          ec2.InstanceSize.MICRO,
        ),
        vpc,
        vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
        securityGroups: [dbSg],
        credentials: rds.Credentials.fromSecret(dbSecret),
        databaseName: 'bakery_ops',
        allocatedStorage: 20,
        storageType: rds.StorageType.GP3,
        backupRetention: cdk.Duration.days(3),
        deletionProtection: true,
        multiAz: false,
        publiclyAccessible: false,
        instanceIdentifier: `bakery-${slug}-db`,
      });

      // Allow EC2 to read the DB secret
      dbSecret.grantRead(role);

      rdsEnv = {
        DB_ENGINE: 'postgresql',
        DB_HOST: dbInstance.dbInstanceEndpointAddress,
        DB_PORT: '5432',
        DB_NAME: 'bakery_ops',
        DB_USER: 'bakery',
        // DB_PASSWORD is read from Secrets Manager at startup via user-data script
      };
    }

    // ── SSM Parameters for secrets ────────────────────────────────────────
    // Telegram token, LLM key, and admin chat_id are stored in SSM Parameter Store.
    // Create them before deploying:
    //   aws ssm put-parameter --name /bakery/<slug>/TELEGRAM_BOT_TOKEN --type SecureString --value <token>
    //   aws ssm put-parameter --name /bakery/<slug>/LLM_API_KEY --type SecureString --value <key>
    //   aws ssm put-parameter --name /bakery/<slug>/ADMIN_CHAT_ID --type String --value <your-chat-id>
    const ssmPrefix = `/bakery/${slug}`;

    role.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:GetParameters'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter${ssmPrefix}/*`,
      ],
    }));

    // Allow EC2 to call Amazon Bedrock (Nova models for agent loop)
    role.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: ['arn:aws:bedrock:*::foundation-model/amazon.nova-*'],
    }));

    // ── EC2 user-data ─────────────────────────────────────────────────────
    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      // System setup
      'set -e',
      'yum update -y',
      'yum install -y python3.11 python3.11-pip git',

      // Data volume for SQLite (mounted at /data)
      ...(dbEngine === 'sqlite' ? [
        'mkdir -p /data',
        // EBS volume is attached as /dev/xvdf — format if new
        'if ! blkid /dev/xvdf; then mkfs.ext4 /dev/xvdf; fi',
        'mount /dev/xvdf /data',
        'echo "/dev/xvdf /data ext4 defaults,nofail 0 2" >> /etc/fstab',
      ] : []),

      // Clone / pull app
      'mkdir -p /opt/bakery',
      'git clone https://github.com/YOUR_ORG/bakery-ops /opt/bakery || (cd /opt/bakery && git pull)',
      'cd /opt/bakery && pip3.11 install -r requirements.txt',

      // Fetch secrets from SSM
      `TELEGRAM_BOT_TOKEN=$(aws ssm get-parameter --name ${ssmPrefix}/TELEGRAM_BOT_TOKEN --with-decryption --query Parameter.Value --output text)`,
      `LLM_API_KEY=$(aws ssm get-parameter --name ${ssmPrefix}/LLM_API_KEY --with-decryption --query Parameter.Value --output text)`,
      `ADMIN_CHAT_ID=$(aws ssm get-parameter --name ${ssmPrefix}/ADMIN_CHAT_ID --query Parameter.Value --output text 2>/dev/null || echo "")`,
      `OWNER_CHAT_ID=$(aws ssm get-parameter --name ${ssmPrefix}/OWNER_CHAT_ID --query Parameter.Value --output text 2>/dev/null || echo "")`,

      // Fetch RDS password if needed
      ...(dbEngine === 'rds' ? [
        `DB_PASSWORD=$(aws secretsmanager get-secret-value --secret-id bakery-${slug}-db-credentials --query SecretString --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")`,
      ] : []),

      // Write .env file
      'cat > /opt/bakery/.env << EOF',
      `TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN`,
      `LLM_API_KEY=$LLM_API_KEY`,
      `LLM_MODEL=gpt-4.1-nano`,
      `ADMIN_CHAT_ID=$ADMIN_CHAT_ID`,
      `OWNER_CHAT_ID=$OWNER_CHAT_ID`,
      ...Object.entries({ ...backupEnv, ...rdsEnv }).map(([k, v]) => `${k}=${v}`),
      ...(dbEngine === 'rds' ? ['DB_PASSWORD=$DB_PASSWORD'] : []),
      'EOF',

      // Run database migrations
      'cd /opt/bakery && python3.11 -m alembic upgrade head',

      // Create systemd service
      'cat > /etc/systemd/system/bakery-bot.service << EOF',
      '[Unit]',
      'Description=Bakery Operations Bot',
      'After=network.target',
      '',
      '[Service]',
      'Type=simple',
      'User=root',
      'WorkingDirectory=/opt/bakery',
      'ExecStart=/usr/bin/python3.11 -m app.telegram_listener',
      'Restart=always',
      'RestartSec=10',
      'EnvironmentFile=/opt/bakery/.env',
      '',
      '[Install]',
      'WantedBy=multi-user.target',
      'EOF',

      'systemctl daemon-reload',
      'systemctl enable bakery-bot',
      'systemctl start bakery-bot',
    );

    // ── EC2 instance ──────────────────────────────────────────────────────
    const instance = new ec2.Instance(this, 'App', {
      instanceName: `bakery-${slug}-app`,
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
      // Prevent accidental termination
      disableApiTermination: true,
      // Encrypt root volume at rest
      blockDevices: [{
        deviceName: '/dev/xvda',
        volume: ec2.BlockDeviceVolume.ebs(20, {
          volumeType: ec2.EbsDeviceVolumeType.GP3,
          encrypted: true,
        }),
      }],
    });

    // ── EBS data volume (SQLite only) ─────────────────────────────────────
    if (dbEngine === 'sqlite') {
      const dataVolume = new ec2.Volume(this, 'DataVolume', {
        volumeName: `bakery-${slug}-data`,
        availabilityZone: instance.instanceAvailabilityZone,
        size: cdk.Size.gibibytes(8),
        volumeType: ec2.EbsDeviceVolumeType.GP3,
        encrypted: true,
        removalPolicy: cdk.RemovalPolicy.RETAIN, // never delete data on stack destroy
      });

      new ec2.CfnVolumeAttachment(this, 'DataVolumeAttachment', {
        instanceId: instance.instanceId,
        volumeId: dataVolume.volumeId,
        device: '/dev/xvdf',
      });
    }

    // ── Outputs ───────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'InstanceId', {
      value: instance.instanceId,
      description: 'EC2 instance ID (use SSM Session Manager to connect)',
    });

    if (backupBucket) {
      new cdk.CfnOutput(this, 'BackupBucket', {
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

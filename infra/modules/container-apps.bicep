// ============================================================================
// Container Apps — Backend, Frontend, Worker + Jobs
// Why Container Apps: Simplest secure managed option, native Job support,
// no K8s cluster management, built-in scaling, managed identity integration.
// ============================================================================

param prefix string
param location string
param tags object
param environment string
param imageTag string
param acrLoginServer string
param appInsightsConnectionString string
param logAnalyticsWorkspaceId string
param keyVaultName string
param databaseHost string
@description('Run the hourly Stripe step meter with --live instead of --dry-run')
param stripeStepMeterLive bool = false
@description('Run the hourly Stripe seat-sync processor with --live instead of --dry-run')
param stripeSeatSyncLive bool = false

// ── Container Apps Environment ───────────────────────────────────────
resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceId, '2023-09-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceId, '2023-09-01').primarySharedKey
      }
    }
    daprAIConnectionString: appInsightsConnectionString
  }
}

// ── User-Assigned Managed Identity ───────────────────────────────────
resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-identity'
  location: location
  tags: tags
}

// ── Backend API ──────────────────────────────────────────────────────
resource backendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-backend'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: false  // Only accessible via APIM
        targetPort: 8000
        transport: 'http'
      }
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        {
          name: 'jwt-secret'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/jwt-secret-key'
          identity: managedIdentity.id
        }
        {
          name: 'db-connection'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/database-url'
          identity: managedIdentity.id
        }
        {
          name: 'openai-key'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/azure-openai-api-key'
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'backend'
          image: '${acrLoginServer}/aigrc-backend:${imageTag}'
          resources: {
            cpu: json(environment == 'prod' ? '1.0' : '0.5')
            memory: environment == 'prod' ? '2Gi' : '1Gi'
          }
          env: [
            { name: 'APP_ENV', value: environment == 'prod' ? 'production' : 'development' }
            { name: 'DATABASE_URL', secretRef: 'db-connection' }
            { name: 'JWT_SECRET_KEY', secretRef: 'jwt-secret' }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'openai-key' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'OTEL_SERVICE_NAME', value: '${prefix}-backend' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: environment == 'prod' ? 2 : 1
        maxReplicas: environment == 'prod' ? 10 : 3
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

// ── Frontend ─────────────────────────────────────────────────────────
resource frontendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-frontend'
  location: location
  tags: tags
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true  // Public-facing
        targetPort: 3000
        transport: 'http'
      }
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: '${acrLoginServer}/aigrc-frontend:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'NEXT_PUBLIC_API_URL', value: 'https://${prefix}-apim.azure-api.net/api/v1' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: environment == 'prod' ? 5 : 2
      }
    }
  }
}

// ── Worker (Dispatcher — continuous) ─────────────────────────────────
resource workerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-worker'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        {
          name: 'db-connection'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/database-url'
          identity: managedIdentity.id
        }
        {
          name: 'openai-key'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/azure-openai-api-key'
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: '${acrLoginServer}/aigrc-worker:${imageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'WORKER_MODE', value: 'dispatcher' }
            { name: 'DATABASE_URL', secretRef: 'db-connection' }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'openai-key' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// ── Risk Analyzer Job (Scheduled) ────────────────────────────────────
resource riskJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-risk-job'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppEnv.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '0 */6 * * *'  // Every 6 hours
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 3600  // 1 hour max
      replicaRetryLimit: 2
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        {
          name: 'db-connection'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/database-url'
          identity: managedIdentity.id
        }
        {
          name: 'openai-key'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/azure-openai-api-key'
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'risk-analyzer'
          image: '${acrLoginServer}/aigrc-worker:${imageTag}'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'WORKER_MODE', value: 'risk_analyzer' }
            { name: 'DATABASE_URL', secretRef: 'db-connection' }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'openai-key' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
        }
      ]
    }
  }
}

// ── Correlation Job (Scheduled) ──────────────────────────────────────
resource correlationJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-corr-job'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppEnv.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '30 */6 * * *'  // 30 min after risk analyzer
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 3600
      replicaRetryLimit: 2
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        {
          name: 'db-connection'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/database-url'
          identity: managedIdentity.id
        }
        {
          name: 'openai-key'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/azure-openai-api-key'
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'correlation-engine'
          image: '${acrLoginServer}/aigrc-worker:${imageTag}'
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'WORKER_MODE', value: 'correlation_engine' }
            { name: 'DATABASE_URL', secretRef: 'db-connection' }
            { name: 'AZURE_OPENAI_API_KEY', secretRef: 'openai-key' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
        }
      ]
    }
  }
}

// ── Device Risk Engine Job (Scheduled) ───────────────────────────────
// Sweeps reported prompt-violation telemetry per tenant and emits coaching
// nudges to at-risk users' enrolled devices. No LLM calls — DB only, so it
// takes neither the OpenAI secret nor the 2Gi footprint the LLM jobs need.
resource deviceRiskJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-devrisk-job'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppEnv.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '0 */4 * * *'  // Every 4h; the 24h per-device cooldown makes repeat sweeps no-ops
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 1800
      replicaRetryLimit: 2
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentity.id
        }
      ]
      secrets: [
        {
          name: 'db-connection'
          keyVaultUrl: 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}/secrets/database-url'
          identity: managedIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'device-risk-engine'
          image: '${acrLoginServer}/aigrc-worker:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'WORKER_MODE', value: 'device_risk_engine' }
            { name: 'DATABASE_URL', secretRef: 'db-connection' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ]
        }
      ]
    }
  }
}

// ── Billing jobs (Scheduled) ─────────────────────────────────────────
// Billing commands from backend/scripts, run from the backend image (which
// ships scripts/) with its default command replaced. They take the database
// and JWT secrets the backend's Settings need to load, and a Stripe key only
// in live mode. See README, "Scheduled billing jobs".
var keyVaultUri = 'https://${keyVaultName}${az.environment().suffixes.keyvaultDns}'

// Stripe step meter (atlas.ai#45, promptly-guide#62). Posts each
// organisation's Work client step overage to Stripe's guided_step meter.
// Idempotent per increment, so a retry or a late extra run never bills twice.
// Dry run unless stripeStepMeterLive is true: it then also references the
// Key Vault secrets stripe-step-meter-key (a restricted key that may write
// meter events and nothing else) and guided-steps-pool-per-user (the Work
// client's steps per provisioned user, a commercial term kept out of this
// public repository), which must both exist before that deploy. Without the
// second the job posts nothing: the overage cannot be told from the pool.
var stepMeterStripeSecret = stripeStepMeterLive
  ? [
      {
        name: 'stripe-step-meter-key'
        keyVaultUrl: '${keyVaultUri}/secrets/stripe-step-meter-key'
        identity: managedIdentity.id
      }
      {
        name: 'guided-steps-pool-per-user'
        keyVaultUrl: '${keyVaultUri}/secrets/guided-steps-pool-per-user'
        identity: managedIdentity.id
      }
    ]
  : []
var stepMeterStripeEnv = stripeStepMeterLive
  ? [
      { name: 'STRIPE_SECRET_KEY', secretRef: 'stripe-step-meter-key' }
      { name: 'GUIDED_STEPS_POOL_PER_USER', secretRef: 'guided-steps-pool-per-user' }
    ]
  : []

resource stepMeterJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-stepmeter-job'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppEnv.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '7 * * * *'  // Hourly, off the hour
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 1800
      replicaRetryLimit: 1  // Safe: each increment has its own idempotency key
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentity.id
        }
      ]
      secrets: concat([
        {
          name: 'db-connection'
          keyVaultUrl: '${keyVaultUri}/secrets/database-url'
          identity: managedIdentity.id
        }
        {
          name: 'jwt-secret'
          keyVaultUrl: '${keyVaultUri}/secrets/jwt-secret-key'
          identity: managedIdentity.id
        }
      ], stepMeterStripeSecret)
    }
    template: {
      containers: [
        {
          name: 'stripe-step-meter'
          image: '${acrLoginServer}/aigrc-backend:${imageTag}'
          command: ['python', '-m', 'scripts.stripe_step_meter']
          args: [stripeStepMeterLive ? '--live' : '--dry-run']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: concat([
            { name: 'APP_ENV', value: environment == 'prod' ? 'production' : 'development' }
            { name: 'DATABASE_URL', secretRef: 'db-connection' }
            { name: 'JWT_SECRET_KEY', secretRef: 'jwt-secret' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ], stepMeterStripeEnv)
        }
      ]
    }
  }
}

// Stripe seat sync (atlas.ai#51). Sends the newest queued sync_seat row in
// billing_outbox for each organisation, with its seat count as it is now, and
// settles older rows as superseded. Idempotent per row and request, so a retry
// or a late extra run never changes a subscription twice. Runs at :37, half an
// hour from the step meter. Dry run unless stripeSeatSyncLive is true: it then
// also references the Key Vault secret stripe-seat-sync-key (a restricted key
// that may write subscriptions and nothing else), which must exist before
// that deploy.
var seatSyncStripeSecret = stripeSeatSyncLive
  ? [
      {
        name: 'stripe-seat-sync-key'
        keyVaultUrl: '${keyVaultUri}/secrets/stripe-seat-sync-key'
        identity: managedIdentity.id
      }
    ]
  : []
var seatSyncStripeEnv = stripeSeatSyncLive
  ? [
      { name: 'STRIPE_SECRET_KEY', secretRef: 'stripe-seat-sync-key' }
    ]
  : []

resource seatSyncJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-seatsync-job'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    environmentId: containerAppEnv.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '37 * * * *'  // Hourly, half an hour from the step meter's :07
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 1800
      replicaRetryLimit: 1  // Safe: each row's request has its own idempotency key
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentity.id
        }
      ]
      secrets: concat([
        {
          name: 'db-connection'
          keyVaultUrl: '${keyVaultUri}/secrets/database-url'
          identity: managedIdentity.id
        }
        {
          name: 'jwt-secret'
          keyVaultUrl: '${keyVaultUri}/secrets/jwt-secret-key'
          identity: managedIdentity.id
        }
      ], seatSyncStripeSecret)
    }
    template: {
      containers: [
        {
          name: 'stripe-seat-sync'
          image: '${acrLoginServer}/aigrc-backend:${imageTag}'
          command: ['python', '-m', 'scripts.stripe_seat_sync']
          args: [stripeSeatSyncLive ? '--live' : '--dry-run']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: concat([
            { name: 'APP_ENV', value: environment == 'prod' ? 'production' : 'development' }
            { name: 'DATABASE_URL', secretRef: 'db-connection' }
            { name: 'JWT_SECRET_KEY', secretRef: 'jwt-secret' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
          ], seatSyncStripeEnv)
        }
      ]
    }
  }
}

// The monthly conversion report (scripts/stripe_conversion.py, atlas.ai#41)
// is deliberately not scheduled: it needs a restricted read-only Stripe key
// the owner has not created yet (promptly-guide decision O8), and its CSV
// belongs in the finance folder, not in job logs. README, "Scheduled billing
// jobs", has the monthly manual step.

output backendUrl string = 'https://${backendApp.properties.configuration.ingress.fqdn}'
output frontendUrl string = 'https://${frontendApp.properties.configuration.ingress.fqdn}'

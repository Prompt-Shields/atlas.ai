using '../main.bicep'

param environment = 'dev'
param location = 'eastus2'
param projectName = 'aigrc'
param imageTag = 'dev-latest'
param acrLoginServer = 'aigrcdev.azurecr.io'

// Hourly Stripe step meter: false = --dry-run (reads only, posts nothing).
// Set to true only once the Key Vault holds stripe-step-meter-key.
// See README, "Scheduled billing jobs".
param stripeStepMeterLive = false

// Hourly Stripe seat sync: false = --dry-run (reads only, sends nothing).
// Set to true only once the Key Vault holds stripe-seat-sync-key.
// See README, "Scheduled billing jobs".
param stripeSeatSyncLive = false

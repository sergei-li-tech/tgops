# Kubernetes Telegram Bot

A Telegram bot for monitoring and managing Kubernetes resources with a focus on Flux HelmReleases.

## Features

- 🔍 Monitor applications across namespaces with label filtering
- 🚨 Identify and troubleshoot unhealthy Flux HelmReleases 
- 🔄 Trigger reconciliation of stalled HelmReleases directly from Telegram
- 📊 Built-in Prometheus metrics for tracking bot usage and performance
- 🔐 User-based access control for secure operations

## Prerequisites

- Python 3.8+
- Kubernetes cluster with Flux CD installed
- Telegram Bot Token (from BotFather)

## Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token from BotFather | Yes | - |
| `ALLOWED_USERS` | Comma-separated list of Telegram user IDs allowed to use the bot | Yes | - |
| `METRICS_PORT` | Port for the Prometheus metrics server | No | 8000 |

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/k8s-telegram-bot.git
   cd k8s-telegram-bot
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables:
   ```bash
   export TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
   export ALLOWED_USERS="123456789,987654321"
   ```

## Deployment to Kubernetes

### Using Helm Chart

The Helm chart for this bot is available in the `gh-pages` branch of this repository under the name "tgops".

1. Add the Helm repository:
   ```bash
   helm repo add tgops https://sergei-li-tech.github.io/tgops/charts
   helm repo update
   ```

2. Install the chart:
   ```bash
   helm install tgops tgops/tgops \
     --set telegramBotToken="your_telegram_bot_token" \
     --set allowedUsers="123456789,987654321"
   ```

### Manual Deployment

Alternatively, you can deploy manually:

1. Create a Kubernetes Secret with your Telegram bot token:
   ```bash
   kubectl create secret generic telegram-bot-secrets \
     --from-literal=TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   ```

2. Apply the deployment manifest:
   ```bash
   kubectl apply -f k8s/deployment.yaml
   ```

## Metrics

The bot exposes Prometheus metrics on the configured port (default: 8000):

- `telegram_bot_commands_total`: Count of commands received by the bot (labels: command, user_id)
- `telegram_bot_callbacks_total`: Count of callback queries processed (labels: action, user_id)
- `telegram_bot_errors_total`: Count of errors encountered (labels: type, command)
- `telegram_bot_unauthorized_attempts_total`: Count of unauthorized access attempts (labels: user_id)
- `telegram_bot_command_latency_seconds`: Command processing latency in seconds (labels: command)

## Available Commands

- `/start` - Introduction to the bot
- `/help` - List available commands
- `/apps` - Display applications with the `tgops=true` label across all namespaces
- `/checkreleases` - List unhealthy Flux HelmReleases and provide options to reconcile them

## Architecture

The bot follows a modular architecture with decorators for metrics collection and access control:

- `measure_latency`: Measures command execution time and reports as Prometheus metrics
- `restricted`: Ensures only authorized users can execute commands
- `restricted_callback`: Ensures only authorized users can trigger callback actions

## Security Considerations

- The bot uses an allowlist approach with explicit user IDs for access control
- All interactions are tracked with user IDs for audit purposes
- Bot needs appropriate RBAC permissions in the Kubernetes cluster to access and modify resources

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

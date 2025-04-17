import os
import asyncio
import json
from datetime import datetime
from functools import wraps
from prometheus_client import start_http_server, Counter, Gauge, Histogram
import time

from kubernetes import client, config
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# Metrics definitions
COMMAND_COUNTER = Counter(
    'telegram_bot_commands_total',
    'Number of commands received by the bot',
    ['command', 'user_id']  # Added user_id to track usage patterns per user
)

CALLBACK_COUNTER = Counter(
    'telegram_bot_callbacks_total',
    'Number of callback queries processed',
    ['action', 'user_id']  # Added user_id for callback tracking
)

ERROR_COUNTER = Counter(
    'telegram_bot_errors_total',
    'Number of errors encountered',
    ['type', 'command']  # Added command to correlate errors with specific commands
)

UNAUTHORIZED_COUNTER = Counter(
    'telegram_bot_unauthorized_attempts_total',
    'Number of unauthorized access attempts',
    ['user_id']  # Track which users are attempting unauthorized access
)

COMMAND_LATENCY = Histogram(
    'telegram_bot_command_latency_seconds',
    'Command processing latency in seconds',
    ['command'],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, float('inf')]  # More meaningful buckets for Telegram bot operations
)

# Existing token and user setup code remains the same
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")

# Parse allowed users from environment variable
allowed_users_str = os.getenv('ALLOWED_USERS', '')
if not allowed_users_str:
    raise ValueError("ALLOWED_USERS environment variable is not set")

try:
    ALLOWED_USERS = [int(user_id.strip()) for user_id in allowed_users_str.split(',')]
    print(f"Authorized users: {ALLOWED_USERS}")
except ValueError as e:
    raise ValueError("ALLOWED_USERS must be a comma-separated list of integers") from e

# Parse application logs map from environment variable
APP_LOGS_MAP = {}
app_logs_str = os.getenv('APP_LOGS_MAP', '')
if app_logs_str:
    try:
        APP_LOGS_MAP = json.loads(app_logs_str)
        print(f"Loaded logs map for {len(APP_LOGS_MAP)} applications")
    except json.JSONDecodeError as e:
        print(f"Warning: Failed to parse APP_LOGS_MAP: {e}")
        print("Expected format: '{\"app1\":\"https://logs-link\",\"app2\":\"https://another-link\"}'")

def measure_latency(command_name):
    """Decorator to measure command latency"""
    def decorator(func):
        @wraps(func)
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            start_time = time.time()
            try:
                result = await func(update, context, *args, **kwargs)
                COMMAND_LATENCY.labels(command=command_name).observe(time.time() - start_time)
                return result
            except Exception as e:
                ERROR_COUNTER.labels(type=type(e).__name__, command=command_name).inc()
                raise
        return wrapped
    return decorator

def restricted(func):
    """Enhanced decorator to restrict bot access and track metrics"""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ALLOWED_USERS:
            UNAUTHORIZED_COUNTER.labels(user_id=str(user_id)).inc()
            print(f"Unauthorized access denied for {user_id}")
            await update.message.reply_text("⛔️ Sorry, you are not authorized to use this bot.")
            return
        command_name = update.message.text.split()[0][1:] if update.message.text else 'unknown'
        COMMAND_COUNTER.labels(command=command_name, user_id=str(user_id)).inc()
        return await func(update, context, *args, **kwargs)
    return wrapped

def restricted_callback(func):
    """Enhanced decorator to restrict callbacks and track metrics"""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.callback_query.from_user.id
        if user_id not in ALLOWED_USERS:
            UNAUTHORIZED_COUNTER.labels(user_id=str(user_id)).inc()
            print(f"Unauthorized callback denied for {user_id}")
            await update.callback_query.answer("⛔️ You are not authorized to perform this action.")
            return
        action = update.callback_query.data.split(':')[0]
        CALLBACK_COUNTER.labels(action=action, user_id=str(user_id)).inc()
        return await func(update, context, *args, **kwargs)
    return wrapped


def load_kubernetes_config():
    """Load kubernetes configuration for either in-cluster or local development"""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        try:
            config.load_kube_config()
        except config.ConfigException as e:
            print(f"Could not configure kubernetes: {e}")
            raise

def calculate_age(creation_timestamp):
    """Calculate age of resource from creation timestamp"""
    if not creation_timestamp:
        return "Unknown"
    
    creation_time = creation_timestamp.replace(tzinfo=None)
    age = datetime.utcnow() - creation_time
    
    if age.days > 0:
        return f"{age.days}d"
    hours = age.seconds // 3600
    if hours > 0:
        return f"{hours}h"
    minutes = (age.seconds % 3600) // 60
    return f"{minutes}m"

def extract_version(tag: str) -> str:
    """Extract version number from the beginning of the tag (before first hyphen)"""
    # Split by hyphen and take first part
    parts = tag.split('-')
    if parts:
        return parts[0]  # Returns everything before first hyphen (e.g., "1.9.0")
    return tag  # Return original tag if no hyphen found

@restricted
@measure_latency('apps')
async def apps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler to search for pods with specific labels across all namespaces"""
    try:
        v1 = client.CoreV1Api()
        # Use label selector to find pods with specific label
        pods = v1.list_pod_for_all_namespaces(label_selector="tgops=true")
        
        if not pods.items:
            await update.message.reply_text("No pods found with label tgops=true")
            return

        response = ""
        
        for pod in pods.items:
            status = pod.status.phase
            status_emoji = "🟢" if status == "Running" else "🔴" if status == "Failed" else "🟡"
            
            # Find container starting with 'main-'
            main_container = None
            for container in pod.spec.containers:
                if container.name.startswith("main-"):
                    main_container = container
                    break
            
            if main_container:
                # Extract tag and version from image
                image = main_container.image
                tag = image.split(":")[-1] if ":" in image else "latest"
                version = tag.split('-')[0]
                
                response += (f"{status_emoji} {pod.metadata.namespace}/{pod.metadata.name}\n"
                           f"   Status: {status}\n"
                           f"   Image tag: {tag}\n"
                           f"   Version: {version}\n"
                           f"   Age: {calculate_age(pod.metadata.creation_timestamp)}\n\n")
            
        await update.message.reply_text(response)
    except Exception as e:
        ERROR_COUNTER.labels(type=type(e).__name__).inc()
        await update.message.reply_text(f"Error searching pods: {str(e)}")

@restricted
@measure_latency('logs')
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler to show log links for applications"""
    try:
        if not APP_LOGS_MAP:
            await update.message.reply_text("No application log links are configured. Set the APP_LOGS_MAP environment variable.")
            return
        
        message = "📋 Application Log Links:\n\n"
        
        # If specific app name provided, show just that app
        if context.args and len(context.args) > 0:
            app_name = context.args[0].lower()
            found = False
            
            for name, url in APP_LOGS_MAP.items():
                if app_name in name.lower():
                    message += f"🔍 [{name}]({url})\n\n"
                    found = True
            
            if not found:
                message = f"No log links found for application matching '{app_name}'"
        else:
            # Otherwise show all apps
            for name, url in APP_LOGS_MAP.items():
                message += f"📊 [{name}]({url})\n\n"
        
        await update.message.reply_text(
            message, 
            parse_mode='Markdown',
            disable_web_page_preview=True  # Don't show link previews
        )
    except Exception as e:
        ERROR_COUNTER.labels(type=type(e).__name__, command='logs').inc()
        await update.message.reply_text(f"Error retrieving log links: {str(e)}")

async def suspend_release(namespace: str, name: str) -> bool:
    """Suspend a HelmRelease"""
    custom_api = client.CustomObjectsApi()
    
    try:
        # First get the current resource
        resource = custom_api.get_namespaced_custom_object(
            group="helm.toolkit.fluxcd.io",
            version="v2",
            plural="helmreleases",
            namespace=namespace,
            name=name
        )
        
        # Update the suspend field
        if 'spec' not in resource:
            resource['spec'] = {}
        resource['spec']['suspend'] = True
        
        # Apply the update
        custom_api.patch_namespaced_custom_object(
            group="helm.toolkit.fluxcd.io",
            version="v2",
            plural="helmreleases",
            namespace=namespace,
            name=name,
            body=resource
        )
        return True
    except Exception as e:
        print(f"Error suspending release {namespace}/{name}: {e}")
        return False


async def unsuspend_release(namespace: str, name: str) -> bool:
    """Unsuspend a HelmRelease"""
    custom_api = client.CustomObjectsApi()
    
    try:
        # First get the current resource
        resource = custom_api.get_namespaced_custom_object(
            group="helm.toolkit.fluxcd.io",
            version="v2",
            plural="helmreleases",
            namespace=namespace,
            name=name
        )
        
        # Update the suspend field
        if 'spec' not in resource:
            resource['spec'] = {}
        resource['spec']['suspend'] = False
        
        # Apply the update
        custom_api.patch_namespaced_custom_object(
            group="helm.toolkit.fluxcd.io",
            version="v2",
            plural="helmreleases",
            namespace=namespace,
            name=name,
            body=resource
        )
        return True
    except Exception as e:
        print(f"Error unsuspending release {namespace}/{name}: {e}")
        return False


async def get_unhealthy_helmreleases():
    """Fetch all HelmReleases and return those that are not Ready"""
    custom_api = client.CustomObjectsApi()
    
    try:
        # Fetch all HelmReleases across all namespaces
        helm_releases = custom_api.list_cluster_custom_object(
            group="helm.toolkit.fluxcd.io",
            version="v2",
            plural="helmreleases"
        )
        
        unhealthy_releases = []
        
        for release in helm_releases.get('items', []):
            name = release['metadata']['name']
            namespace = release['metadata']['namespace']
            status = release.get('status', {})
            conditions = status.get('conditions', [])
            
            # Find the Ready and Reconciling conditions
            ready_condition = next(
                (c for c in conditions if c['type'] == 'Ready'),
                None
            )
            reconciling_condition = next(
                (c for c in conditions if c['type'] == 'Reconciling'),
                None
            )
            
            if ready_condition and ready_condition['status'] != 'True':
                # Get the most relevant error message
                error_message = ready_condition.get('message', 'No error message provided')
                
                # Get additional relevant conditions
                stalled = next(
                    (c for c in conditions if c['type'] == 'Stalled' and c['status'] == 'True'),
                    None
                )
                
                # Check if currently reconciling
                is_reconciling = (reconciling_condition and 
                                reconciling_condition['status'] == 'True' and 
                                reconciling_condition['reason'] == 'Progressing')
                
                release_info = {
                    'name': name,
                    'namespace': namespace,
                    'error': error_message,
                    'stalled': bool(stalled),
                    'reconciling': bool(is_reconciling),
                    'last_transition': ready_condition.get('lastTransitionTime', 'Unknown')
                }
                unhealthy_releases.append(release_info)
        
        return unhealthy_releases
        
    except Exception as e:
        print(f"Error fetching HelmReleases: {e}")
        raise


@restricted
@measure_latency('checkreleases')
async def check_releases_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /checkreleases command with metrics"""
    try:
        unhealthy_releases = await get_unhealthy_helmreleases()
        
        if not unhealthy_releases:
            await update.message.reply_text("All HelmReleases are healthy! 🎉")
            return
            
        # Build response message
        response = "⚠️ Found unhealthy HelmReleases:\n\n"
        
        for release in unhealthy_releases:
            # Choose status emoji based on state
            if release['reconciling']:
                status_emoji = "♻️"
            elif release['stalled']:
                status_emoji = "⛔️"
            else:
                status_emoji = "🔄"
            
            response = f"{status_emoji} *{release['namespace']}/{release['name']}*\n"
            if release['reconciling']:
                response += "├─ Status: RECONCILING\n"
            else:
                response += f"├─ Status: {'STALLED' if release['stalled'] else 'NOT READY'}\n"
            response += f"├─ Last Transition: {release['last_transition']}\n"
            response += f"└─ Error: `{release['error']}`\n\n"
            
            await update.message.reply_text(response, parse_mode='Markdown')
            
            # Only show reconcile button if not currently reconciling
            if not release['reconciling']:
                keyboard = [
                    [
                        InlineKeyboardButton(
                            "🔄 Reconcile",
                            callback_data=f"toggle:{release['namespace']}:{release['name']}"
                        )
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Send button in a separate message
                await update.message.reply_text(
                    f"Actions for *{release['namespace']}/{release['name']}*:",
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        
    except Exception as e:
        ERROR_COUNTER.labels(type=type(e).__name__).inc()
        error_message = f"Error checking HelmReleases: {str(e)}"
        await update.message.reply_text(error_message)


@restricted_callback
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()  # Answer the callback query to remove the loading state
    
    try:
        parts = query.data.split(":")
        action = parts[0]
        
        if action == "toggle" and len(parts) == 3:
            namespace = parts[1]
            name = parts[2]
            
            # First suspend
            await query.edit_message_text(f"Suspending release {namespace}/{name}...")
            if await suspend_release(namespace, name):
                # Immediately unsuspend
                await query.edit_message_text(f"Unsuspending release {namespace}/{name}...")
                if await unsuspend_release(namespace, name):
                    await query.edit_message_text(
                        f"✅ Started reconciliation for {namespace}/{name}\n"
                        "Use /checkreleases to see current status"
                    )
                else:
                    await query.edit_message_text(
                        f"❌ Failed to unsuspend release {namespace}/{name}"
                    )
            else:
                await query.edit_message_text(
                    f"❌ Failed to suspend release {namespace}/{name}"
                )
        elif action == "logs" and len(parts) == 2:
            app_name = parts[1]
            if app_name in APP_LOGS_MAP:
                log_url = APP_LOGS_MAP[app_name]
                await query.edit_message_text(
                    f"📊 *{app_name} Logs*\n{log_url}",
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
            else:
                await query.edit_message_text(f"❌ No log link found for {app_name}")
                
    except Exception as e:
        await query.edit_message_text(f"❌ Error processing action: {str(e)}")


@restricted
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command"""
    await update.message.reply_text('Hello! I am a Kubernetes-aware Telegram bot. Use /help to see available commands.')


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command"""
    help_text = """
Available commands:
/checkreleases - List unhealthy Flux HelmReleases and manage them
/apps - List apps status
/logs [app] - Show log links for applications (optional: filter by app name)
    """
    await update.message.reply_text(help_text)


def main():
    """Start the bot with metrics endpoint"""
    # Start Prometheus metrics server
    metrics_port = int(os.getenv('METRICS_PORT', '8000'))
    start_http_server(metrics_port)
    print(f'Metrics server started on port {metrics_port}')
    
    # Load kubernetes config
    load_kubernetes_config()
    
    # Create application
    app = Application.builder().token(TOKEN).build()

    # Add command handlers
    app.add_handler(CommandHandler('start', start_command))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('apps', apps_command))
    app.add_handler(CommandHandler('logs', logs_command))
    app.add_handler(CommandHandler('checkreleases', check_releases_command))
    
    # Add callback query handler for buttons
    app.add_handler(CallbackQueryHandler(button_callback))

    # Start polling
    print('Starting bot...')
    app.run_polling(poll_interval=1)


if __name__ == '__main__':
    main()
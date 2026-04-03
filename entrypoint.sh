#!/bin/bash
set -e

export HERMES_MODEL="${HERMES_MODEL:-anthropic/claude-sonnet-4-6}"

INSTALL_DIR="/opt/hermes-agent"

# Bootstrap persistent volume (v0.6.0 layout)
mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills}
[ -f "$HERMES_HOME/.env" ]        || cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
[ -f "$HERMES_HOME/config.yaml" ] || cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
[ -f "$HERMES_HOME/SOUL.md" ]     || cp "$INSTALL_DIR/docker/SOUL.md" "$HERMES_HOME/SOUL.md"
[ -d "$INSTALL_DIR/skills" ] && python3 "$INSTALL_DIR/tools/skills_sync.py"

# Determine provider from HERMES_MODEL prefix
PROVIDER="${HERMES_MODEL%%/*}"
MODEL_NAME="${HERMES_MODEL#*/}"

python3 -c "
import yaml
cfg_path = '$HERMES_HOME/config.yaml'
with open(cfg_path) as f:
    cfg = yaml.safe_load(f)
cfg.setdefault('model', {})
cfg['model']['default'] = '$MODEL_NAME'
cfg['model']['provider'] = '$PROVIDER'
cfg.setdefault('agent', {})
cfg['agent']['reasoning_effort'] = 'low'
with open(cfg_path, 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False)
"

cd "$INSTALL_DIR"

if [ $# -gt 0 ]; then
  exec hermes "$@"
elif [ -n "$TELEGRAM_BOT_TOKEN" ] || [ -n "$DISCORD_BOT_TOKEN" ] || [ -n "$SLACK_BOT_TOKEN" ]; then
  exec hermes gateway run
else
  echo "No bot tokens set — run 'hermes' interactively via SSH."
  wait
fi

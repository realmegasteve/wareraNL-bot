#!/bin/bash
# Health check script for Discord bot
# Add to crontab: */5 * * * * /home/teun/wareraNL-bot/healthcheck.sh

SERVICE="discord-bot.service"
LOG_FILE="/home/teun/wareraNL-bot/logs/healthcheck.log"

# Create logs directory if it doesn't exist
mkdir -p /home/teun/wareraNL-bot/logs

# Check if service is running
if ! systemctl is-active --quiet "$SERVICE"; then
    echo "$(date): Service is not running, attempting to start..." >> "$LOG_FILE"
    sudo systemctl start "$SERVICE"
    
    # Wait a few seconds and check again
    sleep 5
    if systemctl is-active --quiet "$SERVICE"; then
        echo "$(date): Service restarted successfully" >> "$LOG_FILE"
    else
        echo "$(date): CRITICAL - Failed to restart service" >> "$LOG_FILE"
    fi
else
    # Service is running, check if it's responsive
    # Check if the process is actually doing something (not stuck)
    DISCORD_PID=$(systemctl show -p MainPID --value "$SERVICE")
    if [ "$DISCORD_PID" != "0" ]; then
        # Check if process is zombie or defunct
        PROC_STATE=$(ps -p "$DISCORD_PID" -o state= 2>/dev/null | tr -d ' ')
        if [ "$PROC_STATE" = "Z" ]; then
            echo "$(date): Process is zombie, restarting service..." >> "$LOG_FILE"
            sudo systemctl restart "$SERVICE"
        fi
    fi
fi

# Keep log file size manageable (keep last 1000 lines)
if [ -f "$LOG_FILE" ]; then
    tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp"
    mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

FROM python:3.13-slim

WORKDIR /app

# Copy only requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install dependencies from requirements.txt (including Flask-HTTPAuth)
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create an entrypoint script to run the appropriate service
RUN echo '#!/bin/bash\n\
\n\
# Default commands if not provided\n\
BOT_CMD=${BOT_COMMAND:-"python run_bot.py"}\n\
ADMIN_CMD=${ADMIN_COMMAND:-"gunicorn --bind 0.0.0.0:${ADMIN_PORT:-5000} --access-logfile - --error-logfile - wsgi:application"}\n\
\n\
# Choose which service to run based on SERVICE_TYPE\n\
if [ "$SERVICE_TYPE" = "admin" ]; then\n\
  echo "Starting Admin interface..."\n\
  exec $ADMIN_CMD\n\
elif [ "$SERVICE_TYPE" = "bot" ]; then\n\
  echo "Starting Bot service..."\n\
  exec $BOT_CMD\n\
else\n\
  echo "Starting both Bot and Admin services..."\n\
  # Start the bot in the background\n\
  $BOT_CMD &\n\
  BOT_PID=$!\n\
  # Start admin in the foreground\n\
  $ADMIN_CMD\n\
  wait $BOT_PID\n\
fi' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# Default to running both services (traditional mode)
CMD ["/app/entrypoint.sh"]

FROM node:18-alpine

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies (using npm install instead of npm ci due to lock file sync issues)
RUN npm install --legacy-peer-deps || npm install

# Copy application code
COPY . .

# Make start-local.sh executable
RUN chmod +x start-local.sh

# Expose port
EXPOSE 2001

# Start development server using start-local.sh script
# Environment variables from docker-compose.yml will be available to the script
CMD ["./start-local.sh"]


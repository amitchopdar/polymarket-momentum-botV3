================================================================================
POLYMARKET MOMENTUM BOT V2 - DEPLOYMENT GUIDE (GITHUB & DOCKER OCI)
================================================================================

1. GITHUB REPOSITORY LINK
--------------------------------------------------------------------------------
GitHub URL: https://github.com/amitchopdar/polymarket-momentum-bot.git


2. HOW TO RUN LOCALLY ON MAC
--------------------------------------------------------------------------------
Command:
  cd /Users/kamalasahu/polymarket-bot-v2
  PYTHONPATH=. python3 main.py

Run Unit Tests:
  PYTHONPATH=. ./venv/bin/pytest tests/ -v

Stop Bot:
  Press Ctrl + C in terminal.


3. END-TO-END ORACLE CLOUD DOCKER DEPLOYMENT GUIDE (152.67.66.51)
--------------------------------------------------------------------------------

PHASE 1: LOCAL MAC SETUP & GITHUB PUSH

Step 1: Commit & Push Code to GitHub from Mac Terminal:
--------------------------------------------------------------------------------
cd /Users/kamalasahu/polymarket-bot-v2
git add .
git commit -m "Production Polymarket Momentum Bot Containerized Setup"
git push -u origin main


PHASE 2: ORACLE CLOUD SERVER DEPLOYMENT (152.67.66.51)

Step 1: SSH into Oracle Server:
--------------------------------------------------------------------------------
ssh -i /Users/kamalasahu/Downloads/ssh-key-2026-07-26.key ubuntu@152.67.66.51


Step 2: Install Docker & Docker Compose:
--------------------------------------------------------------------------------
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu


Step 3: Clone GitHub Repo & Setup .env File:
--------------------------------------------------------------------------------
cd /home/ubuntu
git clone https://github.com/amitchopdar/polymarket-momentum-bot.git
cd polymarket-momentum-bot

cat << 'EOF' > .env
EXECUTION_MODE="DRY_RUN"
TELEGRAM_BOT_TOKEN="8827575847:AAHi642Hnf8r2Vk7_XyIQM8ygR-irdP1J3A"
TELEGRAM_CHAT_ID="488798563,835915433"
TELEGRAM_AUTHORIZED_USER_IDS="488798563,835915433"
POLYMARKET_API_KEY=""
POLYMARKET_SECRET=""
POLYMARKET_PASSPHRASE=""
POLYMARKET_PRIVATE_KEY=""
EOF


Step 4: Build & Launch Docker Container:
--------------------------------------------------------------------------------
touch PolyDB_V2.sqlite
docker compose up -d --build


PHASE 3: CONTAINER MANAGEMENT & LOGS

View Live Logs:
  docker logs -f polymarket-bot-v2

Check Container Status:
  docker ps

Restart Container:
  docker compose restart

Pull Updates & Rebuild:
  cd /home/ubuntu/polymarket-momentum-bot
  git pull
  docker compose up -d --build
================================================================================

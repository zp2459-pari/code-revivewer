#!/bin/bash

CONTAINER_NAME="mysql-agent"
DB_ROOT_PASSWORD="Lenovo@123"
DB_NAME="code_review_db"
DB_PORT="3306"
DATA_DIR="$(pwd)/mysql_data"

GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

echo -e "${YELLOW}>>> Checking MySQL environment...${RESET}"

if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Error: Docker not detected.${RESET}"
    echo "Please install Docker: yum install -y docker && systemctl start docker"
    exit 1
fi

echo -e "Checking container: ${CONTAINER_NAME}..."
CONTAINER_ID=$(docker ps -a -q -f name="^${CONTAINER_NAME}$")

if [ -n "$CONTAINER_ID" ]; then
    STATUS=$(docker inspect -f '{{.State.Running}}' $CONTAINER_ID)
    
    if [ "$STATUS" == "true" ]; then
        echo -e "${GREEN}✅ MySQL ($CONTAINER_NAME) is running${RESET}"
        echo -e "Address: 127.0.0.1:${DB_PORT}"
        echo -e "Database: ${DB_NAME}"
        exit 0
    else
        echo -e "${YELLOW}⚠️  MySQL ($CONTAINER_NAME) exists but is stopped.${RESET}"
        echo "Attempting to start..."
        docker start $CONTAINER_ID
        if [ $? -eq 0 ]; then
             echo -e "${GREEN}✅ Started successfully!${RESET}"
             exit 0
        else
             echo -e "${RED}❌ Failed to start. Check logs: docker logs ${CONTAINER_NAME}${RESET}"
             exit 1
        fi
    fi
else
    PORT_CHECK=$(netstat -tuln | grep ":${DB_PORT} ")
    if [ -n "$PORT_CHECK" ]; then
        echo -e "${RED}❌ Error: Port ${DB_PORT} is in use!${RESET}"
        echo "MySQL might be running locally or another container is using this port."
        echo "Suggestion: Change DB_PORT in the script or stop the conflicting service."
        exit 1
    fi

    echo -e "${YELLOW}>>> Container not found. Starting fresh MySQL installation...${RESET}"
    
    docker run -d \
        --name "${CONTAINER_NAME}" \
        --restart unless-stopped \
        -p "${DB_PORT}:3306" \
        -v "${DATA_DIR}:/var/lib/mysql" \
        -e MYSQL_ROOT_PASSWORD="${DB_ROOT_PASSWORD}" \
        -e MYSQL_DATABASE="${DB_NAME}" \
        mysql:8.0

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}==============================================${RESET}"
        echo -e "${GREEN}🎉 MySQL installed and started successfully!${RESET}"
        echo -e "${GREEN}==============================================${RESET}"
        echo -e "Container Name: ${CONTAINER_NAME}"
        echo -e "Port Mapping: ${DB_PORT} -> 3306"
        echo -e "Data Directory: ${DATA_DIR} (Do not delete)"
        echo -e "Root Password: ${DB_ROOT_PASSWORD}"
        echo -e "Database Name: ${DB_NAME}"
        echo -e "${YELLOW}Please wait 10-20 seconds for initialization...${RESET}"
    else
        echo -e "${RED}❌ Installation failed. Please check Docker status.${RESET}"
        exit 1
    fi
fi
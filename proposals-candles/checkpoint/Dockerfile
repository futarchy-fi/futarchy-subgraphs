FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install --ignore-scripts
COPY . .
RUN node scripts/patch-graphnode-style.js
RUN npx checkpoint generate
RUN npx tsc --noEmit false --skipLibCheck true
EXPOSE 3000
CMD ["npm", "start"]

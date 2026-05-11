/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    SUPABASE_URL: process.env.SUPABASE_URL,
    SUPABASE_KEY: process.env.SUPABASE_KEY,
    ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
    MCP_SERVER_PATH: process.env.MCP_SERVER_PATH,
    BUSINESS_NAME: process.env.BUSINESS_NAME,
    OWNER_NAME: process.env.OWNER_NAME,
  },
};

module.exports = nextConfig;

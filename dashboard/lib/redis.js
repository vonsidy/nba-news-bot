import { Redis } from "@upstash/redis";

// Supports both naming schemes: Upstash's own integration and Vercel KV
const url = process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL;
const token = process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN;

export const redis = url && token ? new Redis({ url, token }) : null;

export function requireRedis() {
  if (!redis) {
    throw new Error(
      "Redis is not configured. Add the Upstash Redis integration in Vercel " +
      "(Storage tab) so UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN are set."
    );
  }
  return redis;
}

'use strict';

const BASE_URL = (process.env.MTGATE_API_URL || 'http://localhost:8889') + '/api';
const TOKEN = process.env.MTGATE_API_TOKEN || '';

async function request(method, path, body) {
  const res = await fetch(`${BASE_URL}${path}`, {
    method,
    headers: {
      'Authorization': `Bearer ${TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`MTGate API ${method} ${path} -> ${res.status}: ${text}`);
  }
  return res.json();
}

async function createProxyUser(name, ttlDays) {
  return request('POST', '/users', { name, ttl_days: ttlDays });
}

async function getProxyUser(name) {
  return request('GET', `/users/${encodeURIComponent(name)}`);
}

async function extendProxyUser(name, days) {
  return request('POST', `/users/${encodeURIComponent(name)}/extend`, { days });
}

async function toggleProxyUser(name) {
  return request('POST', `/users/${encodeURIComponent(name)}/toggle`);
}

async function deleteProxyUser(name) {
  return request('DELETE', `/users/${encodeURIComponent(name)}`);
}

module.exports = { createProxyUser, getProxyUser, extendProxyUser, toggleProxyUser, deleteProxyUser };

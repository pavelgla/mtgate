'use strict';

const Database = require('better-sqlite3');
const path = require('path');

const DB_PATH = process.env.DB_PATH || path.join(__dirname, '../data/subscriptions.db');

let db;

function getDb() {
  if (!db) {
    db = new Database(DB_PATH);
    db.pragma('journal_mode = WAL');
    db.pragma('foreign_keys = ON');
  }
  return db;
}

function initDatabase() {
  const db = getDb();

  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id                INTEGER PRIMARY KEY AUTOINCREMENT,
      telegram_id       INTEGER UNIQUE NOT NULL,
      telegram_username TEXT,
      mtgate_username   TEXT UNIQUE,
      mtgate_secret     TEXT,
      created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS subscriptions (
      id                INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id           INTEGER REFERENCES users(id),
      plan              TEXT NOT NULL CHECK(plan IN ('trial','1month','3month')),
      status            TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','expired','cancelled')),
      started_at        DATETIME NOT NULL,
      expires_at        DATETIME NOT NULL,
      notified_expiring INTEGER DEFAULT 0,
      created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS payments (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id          INTEGER REFERENCES users(id),
      robokassa_inv_id INTEGER UNIQUE,
      amount           REAL NOT NULL,
      plan             TEXT NOT NULL,
      status           TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','failed')),
      paid_at          DATETIME,
      created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
    );
  `);
}

function createUser(telegramId, username) {
  const db = getDb();
  const stmt = db.prepare(`
    INSERT INTO users (telegram_id, telegram_username)
    VALUES (?, ?)
    ON CONFLICT(telegram_id) DO UPDATE SET telegram_username = excluded.telegram_username
    RETURNING *
  `);
  return stmt.get(telegramId, username || null);
}

function getUserByTelegramId(telegramId) {
  const db = getDb();
  return db.prepare('SELECT * FROM users WHERE telegram_id = ?').get(telegramId) || null;
}

function getUserById(id) {
  const db = getDb();
  return db.prepare('SELECT * FROM users WHERE id = ?').get(id) || null;
}

function updateUserMtgate(userId, mtgateUsername, mtgateSecret) {
  const db = getDb();
  db.prepare(`
    UPDATE users SET mtgate_username = ?, mtgate_secret = ? WHERE id = ?
  `).run(mtgateUsername, mtgateSecret, userId);
}

function createSubscription(userId, plan, days) {
  const db = getDb();
  const now = new Date();
  const expiresAt = new Date(now.getTime() + days * 24 * 60 * 60 * 1000);
  const startedAt = now.toISOString().replace('T', ' ').slice(0, 19);
  const expiresAtStr = expiresAt.toISOString().replace('T', ' ').slice(0, 19);

  return db.prepare(`
    INSERT INTO subscriptions (user_id, plan, started_at, expires_at)
    VALUES (?, ?, ?, ?)
    RETURNING *
  `).get(userId, plan, startedAt, expiresAtStr);
}

function getActiveSubscription(userId) {
  const db = getDb();
  return db.prepare(`
    SELECT * FROM subscriptions
    WHERE user_id = ? AND status = 'active' AND expires_at > datetime('now')
    ORDER BY expires_at DESC
    LIMIT 1
  `).get(userId) || null;
}

function expireSubscription(subscriptionId) {
  const db = getDb();
  db.prepare(`
    UPDATE subscriptions SET status = 'expired' WHERE id = ?
  `).run(subscriptionId);
}

function createPayment(userId, plan, amount) {
  const db = getDb();
  const payment = db.prepare(`
    INSERT INTO payments (user_id, plan, amount)
    VALUES (?, ?, ?)
    RETURNING *
  `).get(userId, plan, amount);

  // Use the auto-generated id as robokassa_inv_id
  db.prepare('UPDATE payments SET robokassa_inv_id = ? WHERE id = ?').run(payment.id, payment.id);
  return db.prepare('SELECT * FROM payments WHERE id = ?').get(payment.id);
}

function failPayment(paymentId) {
  const db = getDb();
  db.prepare(`UPDATE payments SET status = 'failed' WHERE id = ?`).run(paymentId);
}

function completePayment(invId) {
  const db = getDb();
  const paidAt = new Date().toISOString().replace('T', ' ').slice(0, 19);
  db.prepare(`
    UPDATE payments SET status = 'completed', paid_at = ? WHERE robokassa_inv_id = ?
  `).run(paidAt, invId);
  return db.prepare('SELECT * FROM payments WHERE robokassa_inv_id = ?').get(invId) || null;
}

function getPaymentByInvId(invId) {
  const db = getDb();
  return db.prepare('SELECT * FROM payments WHERE robokassa_inv_id = ?').get(invId) || null;
}

function getExpiringSoon(days) {
  const db = getDb();
  return db.prepare(`
    SELECT s.*, u.telegram_id, u.telegram_username, u.mtgate_username
    FROM subscriptions s
    JOIN users u ON u.id = s.user_id
    WHERE s.status = 'active'
      AND s.notified_expiring = 0
      AND s.expires_at > datetime('now')
      AND s.expires_at <= datetime('now', ? || ' days')
  `).all(String(days));
}

function getExpired() {
  const db = getDb();
  return db.prepare(`
    SELECT s.*, u.telegram_id, u.telegram_username, u.mtgate_username
    FROM subscriptions s
    JOIN users u ON u.id = s.user_id
    WHERE s.status = 'active'
      AND s.expires_at <= datetime('now')
  `).all();
}

function markNotified(subscriptionId) {
  const db = getDb();
  db.prepare('UPDATE subscriptions SET notified_expiring = 1 WHERE id = ?').run(subscriptionId);
}

function hasUsedTrial(userId) {
  const db = getDb();
  const row = db.prepare(`
    SELECT COUNT(*) AS cnt FROM subscriptions WHERE user_id = ? AND plan = 'trial'
  `).get(userId);
  return row.cnt > 0;
}

module.exports = {
  initDatabase,
  createUser,
  getUserByTelegramId,
  getUserById,
  updateUserMtgate,
  createSubscription,
  getActiveSubscription,
  expireSubscription,
  createPayment,
  completePayment,
  failPayment,
  getPaymentByInvId,
  getExpiringSoon,
  getExpired,
  markNotified,
  hasUsedTrial,
};

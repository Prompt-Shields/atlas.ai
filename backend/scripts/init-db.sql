-- Initialize pgvector extension and schemas
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Schema separation
CREATE SCHEMA IF NOT EXISTS grc;
CREATE SCHEMA IF NOT EXISTS audit;

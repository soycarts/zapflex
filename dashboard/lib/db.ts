import { Pool } from "pg";

// Lazy server-side Postgres pool against Supabase (session pooler). Created on first
// query so the build step never needs DATABASE_URL.
let pool: Pool | null = null;

function getPool(): Pool {
  if (!pool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) throw new Error("DATABASE_URL not set");
    pool = new Pool({
      connectionString,
      max: 4,
      ssl: { rejectUnauthorized: false },
    });
  }
  return pool;
}

export async function q<T = Record<string, unknown>>(
  sql: string,
  params: unknown[] = []
): Promise<T[]> {
  // Use the simple query protocol when there are no params, so the transaction
  // pooler (pgbouncer transaction mode) never sees a lingering prepared statement.
  const res = params.length
    ? await getPool().query(sql, params)
    : await getPool().query(sql);
  return res.rows as T[];
}

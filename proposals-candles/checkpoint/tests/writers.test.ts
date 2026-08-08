import { describe, it, expect, vi, beforeEach } from 'vitest';

// In-memory DB shared with the mocked .checkpoint models.
const { db } = vi.hoisted(() => ({ db: new Map<string, Record<string, any>>() }));

vi.mock('../.checkpoint/models', () => {
    class MockModel {
        [key: string]: any;
        static tableName: string;
        constructor(id: string, indexer: string) {
            this.id = id;
            this._indexer = indexer;
        }
        static async loadEntity(this: any, id: string, indexer: string) {
            const row = db.get(`${this.tableName}:${indexer}:${id}`);
            if (!row) return null;
            const m = new this(id, indexer);
            Object.assign(m, row);
            return m;
        }
        async save() {
            const table = (this.constructor as any).tableName;
            const { _indexer, ...fields } = this;
            db.set(`${table}:${_indexer}:${this.id}`, { ...fields });
        }
    }
    class WhitelistedToken extends MockModel { static tableName = 'whitelistedtokens'; }
    class Proposal extends MockModel { static tableName = 'proposals'; }
    class Pool extends MockModel { static tableName = 'pools'; }
    class Candle extends MockModel { static tableName = 'candles'; }
    class Swap extends MockModel { static tableName = 'swaps'; }
    return { WhitelistedToken, Proposal, Pool, Candle, Swap };
});

// Real mainnet swap: tx 0x2b2bc6e6...b47c on pool 0xa95d...dca3 (YES).
// Trade moved the pool to post-trade sqrtPriceX96 below == 1916.1897 USDS/WETH.
// The average execution price abs(amount1/amount0) == ~1911.74 — a candle must
// NEVER carry that; candles reflect post-trade pool state.
const POOL = '0xa95d30c125c20d001f6aed9f2eff1b8e5577dca3';
const SQRT_PRICE_X96 = 3468157702624309159729023453900n;
const AMOUNT0 = -3878984693865n; // WETH out
const AMOUNT1 = 7415601661056712n; // USDS in

const Q96 = 2 ** 96;
const POST_TRADE_PRICE = (Number(SQRT_PRICE_X96) / Q96) ** 2; // ≈ 1916.1897253416705
const AVG_EXEC_PRICE = Number(-AMOUNT0 > 0n ? AMOUNT1 : -AMOUNT1) / Number(AMOUNT0 < 0n ? -AMOUNT0 : AMOUNT0); // ≈ 1911.74

function seedPool(addr: string, isInverted = 0) {
    db.set(`pools:mainnet:1-${addr}`, {
        id: `1-${addr}`, chain: 1, address: addr,
        token0: '1-0xt0', token1: '1-0xt1', isInverted,
        sqrtPrice: '0', price: '0', liquidity: '0', tick: 0,
        volumeToken0: '0', volumeToken1: '0'
    });
    db.set('whitelistedtokens:mainnet:1-0xt0', { id: '1-0xt0', symbol: 'YES_WETH', decimals: 18 });
    db.set('whitelistedtokens:mainnet:1-0xt1', { id: '1-0xt1', symbol: 'YES_USDS', decimals: 18 });
}

function swapEvent(args: Record<string, any>, address = POOL) {
    return {
        address,
        transactionHash: '0x2b2bc6e6aed9ae87381145090133e4ce37c7a1dd3b8781be501d34dd9183b47c',
        logIndex: 591,
        args: {
            sender: '0xsender', recipient: '0xrecipient',
            amount0: AMOUNT0, amount1: AMOUNT1,
            liquidity: 1000n, tick: 75584,
            ...args
        }
    };
}

async function freshWriters(env: Record<string, string> = {}) {
    vi.resetModules();
    process.env.CANDLE_PERIODS = '3600';
    delete process.env.CANDLE_FLUSH_INTERVAL;
    delete process.env.SKIP_SWAP_STORAGE;
    for (const [k, v] of Object.entries(env)) process.env[k] = v;
    return await import('../src/writers');
}

function candleRow(poolAddr: string, ts: number) {
    const periodStart = Math.floor(ts / 3600) * 3600;
    return db.get(`candles:mainnet:1-${poolAddr}-3600-${periodStart}`);
}

beforeEach(() => {
    db.clear();
});

describe('candle price source: post-trade pool price, never average execution price', () => {
    it('Uniswap V3 swap (named sqrtPriceX96 arg): candle OHLC == sqrtPriceX96-derived price', async () => {
        const { handleSwap } = await freshWriters({ CANDLE_FLUSH_INTERVAL: '1' });
        seedPool(POOL);
        const ts = Math.floor(Date.now() / 1000);

        await handleSwap({
            event: swapEvent({ sqrtPriceX96: SQRT_PRICE_X96 }),
            source: { indexer: 'mainnet' },
            block: { timestamp: ts, number: 25707839 }
        } as any);

        const candle = candleRow(POOL, ts);
        expect(candle).toBeDefined();
        expect(parseFloat(candle!.close)).toBeCloseTo(POST_TRADE_PRICE, 6);
        expect(parseFloat(candle!.high)).toBeCloseTo(POST_TRADE_PRICE, 6);
        // and explicitly NOT the average execution price (~1911.74)
        expect(Math.abs(parseFloat(candle!.close) - AVG_EXEC_PRICE)).toBeGreaterThan(1);

        const swap = [...db.entries()].find(([k]) => k.startsWith('swaps:'))?.[1];
        expect(parseFloat(swap!.price)).toBeCloseTo(POST_TRADE_PRICE, 6);
    });

    it('Algebra swap (named price arg): same post-trade price', async () => {
        const { handleSwap } = await freshWriters({ CANDLE_FLUSH_INTERVAL: '1' });
        seedPool(POOL);
        const ts = Math.floor(Date.now() / 1000);

        await handleSwap({
            event: swapEvent({ price: SQRT_PRICE_X96 }),
            source: { indexer: 'mainnet' },
            block: { timestamp: ts, number: 25707839 }
        } as any);

        expect(parseFloat(candleRow(POOL, ts)!.close)).toBeCloseTo(POST_TRADE_PRICE, 6);
    });

    it('inverted pool: candle price == 1 / raw sqrtPrice-derived price', async () => {
        const { handleSwap } = await freshWriters({ CANDLE_FLUSH_INTERVAL: '1' });
        seedPool(POOL, 1);
        const ts = Math.floor(Date.now() / 1000);

        await handleSwap({
            event: swapEvent({ sqrtPriceX96: SQRT_PRICE_X96 }),
            source: { indexer: 'mainnet' },
            block: { timestamp: ts, number: 25707839 }
        } as any);

        expect(parseFloat(candleRow(POOL, ts)!.close)).toBeCloseTo(1 / POST_TRADE_PRICE, 12);
    });
});

describe('candle flush latency (the live-staleness bug)', () => {
    it('near-head swap persists its candle immediately, not after 50 swaps', async () => {
        const { handleSwap } = await freshWriters(); // default CANDLE_FLUSH_INTERVAL=50
        seedPool(POOL);
        const ts = Math.floor(Date.now() / 1000); // block at chain head

        await handleSwap({
            event: swapEvent({ sqrtPriceX96: SQRT_PRICE_X96 }),
            source: { indexer: 'mainnet' },
            block: { timestamp: ts, number: 25707839 }
        } as any);

        // A single swap on a sparse pool at head must be queryable right away.
        const candle = candleRow(POOL, ts);
        expect(candle, 'candle must be flushed to DB immediately when indexing at head').toBeDefined();
        expect(parseFloat(candle!.close)).toBeCloseTo(POST_TRADE_PRICE, 6);
    });

    it('backfill swap (old block) stays batched — no per-swap flush during resync', async () => {
        const { handleSwap } = await freshWriters();
        seedPool(POOL);
        const ts = Math.floor(Date.now() / 1000) - 7 * 86400; // week-old block

        await handleSwap({
            event: swapEvent({ sqrtPriceX96: SQRT_PRICE_X96 }),
            source: { indexer: 'mainnet' },
            block: { timestamp: ts, number: 20000000 }
        } as any);

        expect(candleRow(POOL, ts)).toBeUndefined();
    });
});

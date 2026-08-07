import { describe, it, expect } from 'vitest';
import { enabledIndexers } from '../src/config';

// NOTE: no startup smoke test against a live DB here. checkpoint's
// validateStore() (where the "schema changed" guard lives) only runs inside
// Container.start(), which first calls provider.getNetworkIdentifier() — a
// live eth_chainId RPC to the configured network_node_url — and needs a
// postgres with the btree_gist extension. Reproducing the guard trip also
// requires two sequential process lifecycles (init DB as gnosis-only, then
// restart with mainnet added). That is an integration test against docker +
// public RPCs, not a unit test, so the flag behavior is covered by the config
// tests below and the isolation is enforced by docker-compose.mainnet.yml
// (fresh postgres volume).

// Contract addresses that must be present in the mainnet (evm_1) config
const MAINNET_FUTARCHY_FACTORY = '0xf9369c0F7a84CAC3b7Ef78c837cF7313309D3678';
const UNISWAP_V3_FACTORY = '0x1F98431c8aD98523631AE4a59f267346ea31F984';

describe('Defect B: DISABLE_GNOSIS / DISABLE_MAINNET indexer selection', () => {
    it('DISABLE_GNOSIS=true -> only the mainnet (evm_1) indexer with FutarchyFactory + Uniswap V3 factory', () => {
        const indexers = enabledIndexers({ DISABLE_GNOSIS: 'true' });

        expect(Object.keys(indexers)).toEqual(['mainnet']);

        const contracts = (indexers.mainnet.sources ?? []).map(s => s.contract);
        expect(contracts).toContain(MAINNET_FUTARCHY_FACTORY);
        expect(contracts).toContain(UNISWAP_V3_FACTORY);
        expect(contracts).toHaveLength(2);
    });

    it('no flags -> both networks present', () => {
        const indexers = enabledIndexers({});
        expect(Object.keys(indexers).sort()).toEqual(['gnosis', 'mainnet']);
    });

    it('DISABLE_MAINNET=true -> only the gnosis indexer', () => {
        const indexers = enabledIndexers({ DISABLE_MAINNET: 'true' });
        expect(Object.keys(indexers)).toEqual(['gnosis']);
    });
});

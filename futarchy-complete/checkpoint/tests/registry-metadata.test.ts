import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the generated checkpoint models so writers run without a DB.
vi.mock('../.checkpoint/models', () => {
    class Base {
        static loadEntity = vi.fn();
        id: string;
        constructor(id: string) {
            this.id = id;
        }
        save = vi.fn();
        delete = vi.fn();
    }
    class Aggregator extends Base {}
    class Organization extends Base {}
    class ProposalEntity extends Base {}
    class MetadataEntry extends Base {}
    return { Aggregator, Organization, ProposalEntity, MetadataEntry };
});

import { config } from '../src/config';
import { ProposalMetadataAbi } from '../src/abis';
import { writers } from '../src/writers';
import { ProposalEntity } from '../.checkpoint/models';

const PROPOSAL_METADATA = '0x8067b779f2f04ec9758f9192b3ea557fc313fe78';
const ORG = '0xe27f436c4ca24944bda49d18020aec44cc769396';

beforeEach(() => {
    (ProposalEntity.loadEntity as any).mockReset();
});

describe('Defect A1: updateMetadata() -> MetadataUpdated(string,string,string)', () => {
    // On-chain evidence: tx 0x4582b1d956c8f8fa01bd2c3555ad88bb54295b4847d1fad9eced7ca2948560b4
    // emits topic0 keccak('MetadataUpdated(string,string,string)') =
    // 0x61b45807b5528344b8b2c26433a3aabead6c9dc6239e146e7ba7c812fded07d0
    const SIG = 'MetadataUpdated(string,string,string)';

    it('registers the exact ABI event signature on the ProposalMetadata template', () => {
        const ev = config.templates?.ProposalMetadata?.events?.find(e => e.name === SIG);
        expect(ev, `ProposalMetadata template must handle ${SIG}`).toBeDefined();

        // The runtime ABI must contain the same event so checkpoint can decode args
        const abiEvent = (ProposalMetadataAbi as readonly any[]).find(
            e => e.type === 'event' && e.name === 'MetadataUpdated'
        );
        expect(abiEvent, 'ProposalMetadataAbi must define event MetadataUpdated').toBeDefined();
        expect(abiEvent.inputs.map((i: any) => i.type)).toEqual(['string', 'string', 'string']);
    });

    it('updates title/displayNameEvent/description on the proposal entity', async () => {
        const ev = config.templates?.ProposalMetadata?.events?.find(e => e.name === SIG);
        expect(ev).toBeDefined();
        const handler = (writers as any)[ev!.fn];
        expect(handler).toBeTypeOf('function');

        const entity: any = { title: 'old', displayNameEvent: 'old', description: 'old', save: vi.fn() };
        (ProposalEntity.loadEntity as any).mockResolvedValue(entity);

        await handler({
            event: { args: { displayNameQuestion: 'Q', displayNameEvent: 'E', description: 'new description' } },
            source: { contract: PROPOSAL_METADATA }
        });

        expect(ProposalEntity.loadEntity).toHaveBeenCalledWith(PROPOSAL_METADATA, 'gnosis');
        expect(entity.description).toBe('new description');
        expect(entity.title).toBe('Q');
        expect(entity.displayNameEvent).toBe('E');
        expect(entity.save).toHaveBeenCalled();
    });
});

describe('Defect A2: removeProposalMetadata() -> ProposalRemoved(address)', () => {
    // On-chain evidence: tx 0x68f0988200db1535a043f53c8e26adca0e8ad4ebb276bee02ba7f9f3f6058f1d
    // emits topic0 keccak('ProposalRemoved(address)') =
    // 0x6e52ad0a2da1948b4bcaea587cea7e8f6965dc22ff43df8c009597052efb6355
    const SIG = 'ProposalRemoved(address)';

    it('deletes the proposal entity', async () => {
        const ev = config.templates?.Organization?.events?.find(e => e.name === SIG);
        expect(ev, `Organization template must handle ${SIG}`).toBeDefined();
        const handler = (writers as any)[ev!.fn];
        expect(handler).toBeTypeOf('function');

        const entity: any = { save: vi.fn(), delete: vi.fn() };
        (ProposalEntity.loadEntity as any).mockResolvedValue(entity);

        await handler({
            event: { args: { proposalMetadata: PROPOSAL_METADATA } },
            source: { contract: ORG }
        });

        expect(ProposalEntity.loadEntity).toHaveBeenCalledWith(PROPOSAL_METADATA, 'gnosis');
        expect(entity.delete).toHaveBeenCalled();
    });
});

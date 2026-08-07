export const ProposalMetadataAbi = [
    // Events
    {
        anonymous: false,
        inputs: [
            { indexed: false, internalType: 'string', name: 'displayNameQuestion', type: 'string' },
            { indexed: false, internalType: 'string', name: 'displayNameEvent', type: 'string' },
            { indexed: false, internalType: 'string', name: 'description', type: 'string' }
        ],
        name: 'MetadataUpdated',
        type: 'event'
    },
    {
        anonymous: false,
        inputs: [
            { indexed: false, internalType: 'string', name: 'metadata', type: 'string' },
            { indexed: false, internalType: 'string', name: 'metadataURI', type: 'string' }
        ],
        name: 'ExtendedMetadataUpdated',
        type: 'event'
    },
    {
        anonymous: false,
        inputs: [
            { indexed: true, internalType: 'address', name: 'newEditor', type: 'address' }
        ],
        name: 'EditorSet',
        type: 'event'
    },
    {
        anonymous: false,
        inputs: [
            { indexed: true, internalType: 'address', name: 'previousOwner', type: 'address' },
            { indexed: true, internalType: 'address', name: 'newOwner', type: 'address' }
        ],
        name: 'OwnershipTransferred',
        type: 'event'
    },
    // View functions
    {
        inputs: [],
        name: 'proposalAddress',
        outputs: [{ internalType: 'address', name: '', type: 'address' }],
        stateMutability: 'view',
        type: 'function'
    },
    {
        inputs: [],
        name: 'displayNameQuestion',
        outputs: [{ internalType: 'string', name: '', type: 'string' }],
        stateMutability: 'view',
        type: 'function'
    },
    {
        inputs: [],
        name: 'displayNameEvent',
        outputs: [{ internalType: 'string', name: '', type: 'string' }],
        stateMutability: 'view',
        type: 'function'
    },
    {
        inputs: [],
        name: 'description',
        outputs: [{ internalType: 'string', name: '', type: 'string' }],
        stateMutability: 'view',
        type: 'function'
    },
    {
        inputs: [],
        name: 'metadata',
        outputs: [{ internalType: 'string', name: '', type: 'string' }],
        stateMutability: 'view',
        type: 'function'
    },
    {
        inputs: [],
        name: 'metadataURI',
        outputs: [{ internalType: 'string', name: '', type: 'string' }],
        stateMutability: 'view',
        type: 'function'
    },
    {
        inputs: [],
        name: 'owner',
        outputs: [{ internalType: 'address', name: '', type: 'address' }],
        stateMutability: 'view',
        type: 'function'
    },
    {
        inputs: [],
        name: 'editor',
        outputs: [{ internalType: 'address', name: '', type: 'address' }],
        stateMutability: 'view',
        type: 'function'
    }
] as const;

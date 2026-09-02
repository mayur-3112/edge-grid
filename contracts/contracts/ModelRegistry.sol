// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Owned} from "./Auth.sol";

/// @title ModelRegistry
/// @notice Binds a model id to the content hash of the weights that id refers
/// to, so a verifier can tell whether the provider ran the model it was paid to
/// run rather than a cheaper quantisation of it.
///
/// The Phase-1 design names this contract; nothing implemented it. Without it
/// `JobRequest.model` is an unenforced string and a provider can serve a 2B
/// model against a request for a 7B one with no on-chain trace.
///
/// Registration is first-come and the registrant becomes the publisher for that
/// id. Versions are monotonic, so a client that pinned version N can tell that
/// the weights behind an id moved underneath it.
contract ModelRegistry is Owned {
    // -- errors ------------------------------------------------------------

    error ModelExists(bytes32 modelId);
    error NoModel(bytes32 modelId);
    error NotPublisher(bytes32 modelId, address caller, address publisher);
    error ModelRevoked(bytes32 modelId);
    error ZeroHash();

    // -- storage -----------------------------------------------------------

    struct Model {
        address publisher;
        bytes32 contentHash;
        uint32 version;
        uint64 registeredAt;
        uint64 updatedAt;
        bool revoked;
        string uri;
    }

    mapping(bytes32 => Model) public models;
    bytes32[] private _modelIds;

    // -- events ------------------------------------------------------------

    event ModelRegistered(bytes32 indexed modelId, address indexed publisher, bytes32 contentHash, string uri);
    event ModelUpdated(bytes32 indexed modelId, bytes32 previousHash, bytes32 contentHash, uint32 version);
    event ModelRevokedEvent(bytes32 indexed modelId, address indexed publisher);
    event PublisherTransferred(bytes32 indexed modelId, address indexed previous, address indexed next);

    constructor() Owned(msg.sender) {}

    modifier onlyPublisher(bytes32 modelId) {
        Model storage m = models[modelId];
        if (m.publisher == address(0)) revert NoModel(modelId);
        if (msg.sender != m.publisher) revert NotPublisher(modelId, msg.sender, m.publisher);
        _;
    }

    // -- mutations ---------------------------------------------------------

    /// @notice Claim `modelId` and bind it to `contentHash`.
    /// @param modelId keccak256 of the model name used in `JobRequest.model`.
    /// @param contentHash Digest of the weights, e.g. the GGUF sha256.
    /// @param uri Where the weights can be fetched.
    function registerModel(bytes32 modelId, bytes32 contentHash, string calldata uri) external {
        if (contentHash == bytes32(0)) revert ZeroHash();
        if (models[modelId].publisher != address(0)) revert ModelExists(modelId);

        models[modelId] = Model({
            publisher: msg.sender,
            contentHash: contentHash,
            version: 1,
            registeredAt: uint64(block.timestamp),
            updatedAt: uint64(block.timestamp),
            revoked: false,
            uri: uri
        });
        _modelIds.push(modelId);

        emit ModelRegistered(modelId, msg.sender, contentHash, uri);
    }

    /// @notice Point an existing id at new weights and bump its version.
    function updateModel(bytes32 modelId, bytes32 contentHash, string calldata uri)
        external
        onlyPublisher(modelId)
    {
        if (contentHash == bytes32(0)) revert ZeroHash();
        Model storage m = models[modelId];
        if (m.revoked) revert ModelRevoked(modelId);

        bytes32 previous = m.contentHash;
        m.contentHash = contentHash;
        m.uri = uri;
        m.version += 1;
        m.updatedAt = uint64(block.timestamp);

        emit ModelUpdated(modelId, previous, contentHash, m.version);
    }

    /// @notice Mark a model withdrawn. Requesters should stop awarding it.
    function revokeModel(bytes32 modelId) external onlyPublisher(modelId) {
        Model storage m = models[modelId];
        if (m.revoked) revert ModelRevoked(modelId);
        m.revoked = true;
        m.updatedAt = uint64(block.timestamp);
        emit ModelRevokedEvent(modelId, msg.sender);
    }

    /// @notice Hand publishing rights for an id to another account.
    function transferPublisher(bytes32 modelId, address next) external onlyPublisher(modelId) {
        if (next == address(0)) revert ZeroAddress();
        emit PublisherTransferred(modelId, models[modelId].publisher, next);
        models[modelId].publisher = next;
    }

    // -- views -------------------------------------------------------------

    /// @notice Content hash for `modelId`. Reverts if unknown or revoked, so a
    /// caller cannot mistake "not registered" for "hash zero".
    function contentHashOf(bytes32 modelId) external view returns (bytes32) {
        Model storage m = models[modelId];
        if (m.publisher == address(0)) revert NoModel(modelId);
        if (m.revoked) revert ModelRevoked(modelId);
        return m.contentHash;
    }

    function modelCount() external view returns (uint256) {
        return _modelIds.length;
    }

    function modelIdAt(uint256 i) external view returns (bytes32) {
        return _modelIds[i];
    }
}

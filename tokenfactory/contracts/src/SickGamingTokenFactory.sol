// SPDX-License-Identifier: MIT
pragma solidity 0.8.37;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";


/// @notice Fixed-supply ERC-20 with no administrator or post-deployment mint path.
contract SickGamingFixedSupplyToken is ERC20 {
    uint8 private immutable _configuredDecimals;

    constructor(
        string memory name_,
        string memory symbol_,
        uint8 decimals_,
        uint256 supplyAtomic_,
        address recipient_
    ) ERC20(name_, symbol_) {
        _configuredDecimals = decimals_;
        _mint(recipient_, supplyAtomic_);
    }

    function decimals() public view override returns (uint8) {
        return _configuredDecimals;
    }
}


/// @notice Deterministic, retry-safe deployment of one reviewed token template.
contract SickGamingTokenFactory {
    uint8 public constant MAX_DECIMALS = 18;
    uint256 public constant MAX_NAME_BYTES = 64;
    uint256 public constant MAX_SYMBOL_BYTES = 10;

    struct Deployment {
        address token;
        bytes32 parametersHash;
    }

    mapping(bytes32 requestId => Deployment deployment) private _deployments;

    error EmptyName();
    error EmptySymbol();
    error InvalidNameLength();
    error InvalidSymbolLength();
    error InvalidDecimals();
    error InvalidSupply();
    error InvalidRecipient();
    error InvalidRequestId();
    error RequestIdConflict();

    event FixedSupplyTokenCreated(
        bytes32 indexed requestId,
        address indexed token,
        address indexed recipient,
        address creator,
        bytes32 parametersHash
    );

    function createFixedSupplyToken(
        string calldata name_,
        string calldata symbol_,
        uint8 decimals_,
        uint256 supplyAtomic_,
        address recipient_,
        bytes32 requestId_
    ) external returns (address token) {
        uint256 nameLength = bytes(name_).length;
        uint256 symbolLength = bytes(symbol_).length;
        if (nameLength == 0) revert EmptyName();
        if (symbolLength == 0) revert EmptySymbol();
        if (nameLength > MAX_NAME_BYTES) revert InvalidNameLength();
        if (symbolLength > MAX_SYMBOL_BYTES) revert InvalidSymbolLength();
        if (decimals_ > MAX_DECIMALS) revert InvalidDecimals();
        if (supplyAtomic_ == 0) revert InvalidSupply();
        if (recipient_ == address(0)) revert InvalidRecipient();
        if (requestId_ == bytes32(0)) revert InvalidRequestId();

        bytes32 parametersHash = keccak256(
            abi.encode(name_, symbol_, decimals_, supplyAtomic_, recipient_)
        );
        Deployment storage existing = _deployments[requestId_];
        if (existing.token != address(0)) {
            if (existing.parametersHash != parametersHash) revert RequestIdConflict();
            return existing.token;
        }

        token = address(
            new SickGamingFixedSupplyToken{salt: requestId_}(
                name_, symbol_, decimals_, supplyAtomic_, recipient_
            )
        );
        _deployments[requestId_] = Deployment(token, parametersHash);
        emit FixedSupplyTokenCreated(
            requestId_,
            token,
            recipient_,
            msg.sender,
            parametersHash
        );
    }

    function deployment(bytes32 requestId_)
        external
        view
        returns (address token, bytes32 parametersHash)
    {
        Deployment storage stored = _deployments[requestId_];
        return (stored.token, stored.parametersHash);
    }
}

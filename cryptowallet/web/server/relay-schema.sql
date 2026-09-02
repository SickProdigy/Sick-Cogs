CREATE TABLE sickwallet_pairing_codes (
    code_digest CHAR(64) CHARACTER SET ascii PRIMARY KEY,
    expires_at BIGINT UNSIGNED NOT NULL,
    consumed_at BIGINT UNSIGNED NULL,
    created_at BIGINT UNSIGNED NOT NULL
) ENGINE=InnoDB;

CREATE TABLE sickwallet_installations (
    installation_id VARCHAR(64) CHARACTER SET ascii PRIMARY KEY,
    credential VARCHAR(128) CHARACTER SET ascii NOT NULL,
    deployment_id VARCHAR(128) CHARACTER SET ascii NOT NULL,
    discord_application_id VARCHAR(32) CHARACTER SET ascii NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    revoked_at BIGINT UNSIGNED NULL,
    INDEX sickwallet_installation_identity (deployment_id, discord_application_id, revoked_at)
) ENGINE=InnoDB;

CREATE TABLE sickwallet_relay_nonces (
    installation_id VARCHAR(64) CHARACTER SET ascii NOT NULL,
    nonce VARCHAR(128) CHARACTER SET ascii NOT NULL,
    seen_at BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (installation_id, nonce),
    CONSTRAINT sickwallet_nonce_installation
        FOREIGN KEY (installation_id) REFERENCES sickwallet_installations (installation_id)
        ON DELETE CASCADE,
    INDEX sickwallet_nonce_expiry (seen_at)
) ENGINE=InnoDB;

CREATE TABLE sickwallet_relay_messages (
    request_id CHAR(36) CHARACTER SET ascii PRIMARY KEY,
    installation_id VARCHAR(64) CHARACTER SET ascii NOT NULL,
    operation VARCHAR(64) CHARACTER SET ascii NOT NULL,
    request_json LONGTEXT NOT NULL,
    result_json LONGTEXT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    expires_at BIGINT UNSIGNED NOT NULL,
    available_at BIGINT UNSIGNED NOT NULL,
    lease_token VARCHAR(128) CHARACTER SET ascii NULL,
    leased_until BIGINT UNSIGNED NULL,
    attempts SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    completed_at BIGINT UNSIGNED NULL,
    acknowledged_at BIGINT UNSIGNED NULL,
    CONSTRAINT sickwallet_message_installation
        FOREIGN KEY (installation_id) REFERENCES sickwallet_installations (installation_id)
        ON DELETE CASCADE,
    INDEX sickwallet_pending (installation_id, completed_at, available_at, expires_at),
    INDEX sickwallet_cleanup (expires_at, acknowledged_at)
) ENGINE=InnoDB;

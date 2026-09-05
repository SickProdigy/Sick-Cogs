CREATE TABLE IF NOT EXISTS sickwallet_recovery_handoffs (
    handoff_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    ciphertext MEDIUMBLOB NOT NULL,
    cipher_nonce VARBINARY(12) NOT NULL,
    cipher_tag VARBINARY(16) NOT NULL,
    expires_at BIGINT UNSIGNED NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    consumed_at BIGINT UNSIGNED NULL,
    INDEX sickwallet_recovery_expiry (expires_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sickwallet_relay_nonces (
    nonce_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    created_at BIGINT UNSIGNED NOT NULL,
    INDEX sickwallet_nonce_created (created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sickwallet_polymarket_onboarding_results (
    handoff_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    ciphertext VARBINARY(2048) NOT NULL,
    cipher_nonce BINARY(12) NOT NULL,
    cipher_tag BINARY(16) NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    expires_at BIGINT UNSIGNED NOT NULL,
    INDEX sickwallet_polymarket_results_expires (expires_at)
) ENGINE=InnoDB;

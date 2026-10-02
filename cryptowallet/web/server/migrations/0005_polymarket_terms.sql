CREATE TABLE IF NOT EXISTS sickwallet_polymarket_terms_acceptances (
    handoff_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    product VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    terms_version VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    acceptance_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    INDEX sickwallet_polymarket_terms_created (created_at)
) ENGINE=InnoDB;

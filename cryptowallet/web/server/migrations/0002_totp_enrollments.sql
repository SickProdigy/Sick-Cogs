CREATE TABLE IF NOT EXISTS sickwallet_totp_enrollments (
    handoff_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    ciphertext VARCHAR(1024) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at BIGINT UNSIGNED NOT NULL,
    INDEX sickwallet_totp_enrollment_created (created_at)
) ENGINE=InnoDB;

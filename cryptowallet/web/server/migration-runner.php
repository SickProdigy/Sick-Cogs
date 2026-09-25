<?php
declare(strict_types=1);

function sickwallet_migration_statements(string $sql): array
{
    $statements = preg_split("/;[[:space:]]*(?:$|\R)/", trim($sql));
    if ($statements === false) {
        throw new RuntimeException("Could not parse a database migration.");
    }
    return array_values(array_filter(
        array_map("trim", $statements),
        static fn (string $statement): bool => $statement !== ""
    ));
}

function sickwallet_apply_migrations(PDO $database, string $directory): array
{
    if (!is_dir($directory) || is_link($directory)) {
        throw new RuntimeException("The database migration directory is unavailable.");
    }
    $database->exec(
        "CREATE TABLE IF NOT EXISTS sickwallet_schema_migrations ("
        . "migration VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,"
        . "checksum CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,"
        . "applied_at BIGINT UNSIGNED NOT NULL"
        . ") ENGINE=InnoDB"
    );
    $lock = $database->query(
        "SELECT GET_LOCK(0x7369636b77616c6c65745f736368656d615f6d6967726174696f6e73, 10)"
    )->fetchColumn();
    if ((string) $lock !== "1") {
        throw new RuntimeException("Could not acquire the database migration lock.");
    }

    try {
        $files = glob($directory . "/*.sql");
        if ($files === false) {
            throw new RuntimeException("Could not enumerate database migrations.");
        }
        sort($files, SORT_STRING);
        $appliedRows = $database->query(
            "SELECT migration, checksum FROM sickwallet_schema_migrations"
        )->fetchAll(PDO::FETCH_KEY_PAIR);
        $applied = [];
        $current = [];

        foreach ($files as $path) {
            if (!is_file($path) || is_link($path)) {
                throw new RuntimeException("A database migration path is invalid.");
            }
            $name = basename($path);
            if (!preg_match("/^[0-9]{4}_[a-z0-9_]+[.]sql$/D", $name)) {
                throw new RuntimeException("A database migration filename is invalid.");
            }
            $sql = file_get_contents($path);
            if ($sql === false || trim($sql) === "") {
                throw new RuntimeException("Database migration " . $name . " is empty.");
            }
            $checksum = hash("sha256", $sql);
            if (array_key_exists($name, $appliedRows)) {
                if (!hash_equals((string) $appliedRows[$name], $checksum)) {
                    throw new RuntimeException(
                        "Applied database migration " . $name . " was modified."
                    );
                }
                $current[] = $name;
                continue;
            }

            foreach (sickwallet_migration_statements($sql) as $statement) {
                $database->exec($statement);
            }
            $record = $database->prepare(
                "INSERT INTO sickwallet_schema_migrations "
                . "(migration, checksum, applied_at) VALUES (?, ?, ?)"
            );
            $record->execute([$name, $checksum, time()]);
            $applied[] = $name;
            $current[] = $name;
        }
        return ["applied" => $applied, "current" => $current];
    } finally {
        $database->query(
            "SELECT RELEASE_LOCK(0x7369636b77616c6c65745f736368656d615f6d6967726174696f6e73)"
        )->fetchColumn();
    }
}

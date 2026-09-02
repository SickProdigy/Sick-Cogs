<?php
declare(strict_types=1);

// Copy outside the public document root, restrict to the website account, and rename.
return [
    'dsn' => 'mysql:host=127.0.0.1;dbname=sickwallet;charset=utf8mb4',
    'username' => 'sickwallet',
    'password' => 'REPLACE_WITH_A_DATABASE_PASSWORD',
];

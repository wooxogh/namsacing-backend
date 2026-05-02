-- Mirror of Django's wasscam_post + similarity phone_checks tables.
-- Kept manually in sync with sinchonApp/wasscam/migrations/0001_initial.py and
-- sinchonApp/similarity/migrations/0001_initial.py because the bench runs
-- without Django's migration system.
--
-- IMPORTANT: This schema reflects what production has TODAY. Two facts that
-- come up in measurements:
--   1) The column is `content`, not `body`. (sinchonApp/similarity/services/db_search.py
--      runs MATCH(title, body) which references a non-existent column.)
--   2) There is no FULLTEXT INDEX in the original migration. We add one here
--      so the FULLTEXT path can at least be measured when the SQL is fixed.

CREATE TABLE IF NOT EXISTS wasscam_post (
    id            BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(200) NOT NULL,
    category      VARCHAR(20)  NOT NULL,
    subcategory   VARCHAR(20)  DEFAULT NULL,
    content       LONGTEXT     NOT NULL,
    created_at    DATETIME(6)  NOT NULL,
    thumbnail_url VARCHAR(200) DEFAULT NULL,
    views         INT UNSIGNED NOT NULL DEFAULT 0,
    author_id     BIGINT       NOT NULL DEFAULT 1,
    KEY idx_category_created (category, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- FULLTEXT index added by bench (NOT present in production migration).
-- Uses ngram parser so Korean tokens of length 2+ are indexed.
ALTER TABLE wasscam_post
    ADD FULLTEXT INDEX ft_title_content (title, content) WITH PARSER ngram;

CREATE TABLE IF NOT EXISTS phone_checks (
    id              BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    number          VARCHAR(32)  NOT NULL UNIQUE,
    spam            VARCHAR(100) DEFAULT NULL,
    spam_count_raw  VARCHAR(32)  DEFAULT NULL,
    spam_count      INT          NOT NULL DEFAULT 0,
    registed_date   VARCHAR(32)  DEFAULT NULL,
    cyber_crime     VARCHAR(255) DEFAULT NULL,
    success         INT          NOT NULL DEFAULT 0,
    last_checked_at DATETIME(6)  NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

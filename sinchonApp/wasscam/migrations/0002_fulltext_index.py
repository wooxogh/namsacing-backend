"""Add FULLTEXT(title, content) index with ngram parser to wasscam_post.

Required for similarity.services.db_search.fetch_candidates_mysql to use the
intended FULLTEXT path. Without this index MySQL throws "Can't find FULLTEXT
index matching the column list" and the function falls back to ORDER BY
created_at DESC.

ngram parser is needed for Korean since the default parser tokenizes on
whitespace and Korean text often lacks whitespace boundaries inside meaningful
units (e.g., "보이스피싱"). innodb_ft_min_token_size=1 + ngram_token_size=2
in MySQL config let bi-grams be indexed.

MySQL-only operation. SQLite/Postgres test envs must skip this migration.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("wasscam", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE wasscam_post "
                "ADD FULLTEXT INDEX ft_title_content (title, content) "
                "WITH PARSER ngram"
            ),
            reverse_sql=(
                "ALTER TABLE wasscam_post DROP INDEX ft_title_content"
            ),
        ),
    ]

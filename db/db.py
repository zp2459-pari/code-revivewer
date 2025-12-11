import pymysql
import os
import sys
import json

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

try:
    from logger import log
except ImportError:
    import logging
    log = logging.getLogger("DB_Manager_Fallback")
    log.setLevel(logging.INFO)
    log.addHandler(logging.StreamHandler())

class DBManager:
    def __init__(self):
        self.host = os.getenv("DB_HOST", "127.0.0.1")
        self.port = int(os.getenv("DB_PORT", 3306))
        self.user = os.getenv("DB_USER", "root")
        self.password = os.getenv("DB_PASSWORD", "Lenovo@123") 
        self.db_name = os.getenv("DB_NAME", "code_review_db")
        
    def get_connection(self):
        try:
            return pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.db_name,
                cursorclass=pymysql.cursors.DictCursor,
                charset='utf8mb4'
            )
        except pymysql.MySQLError as e:
            log.error(f"Database connection failed: {e}")
            raise e

    def init_tables(self):
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                log.info("Checking table: team_rules...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS team_rules (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        category VARCHAR(50) NOT NULL,
                        rule_content TEXT NOT NULL,
                        severity VARCHAR(20) DEFAULT 'WARN',
                        is_active TINYINT(1) DEFAULT 1,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                
                log.info("Checking table: review_history...")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS review_history (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        file_path VARCHAR(255),
                        verdict VARCHAR(20),
                        ai_report TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            conn.commit()
            log.info("Database tables check completed.")
        except Exception as e:
            log.error(f"Failed to init tables: {e}")
        finally:
            if conn:
                conn.close()

    def sync_rules_from_json(self, json_path):
        """Reads JSON and updates the DB. Acts as Single Source of Truth."""
        if not os.path.exists(json_path):
            log.warning(f"Rules JSON file not found: {json_path}")
            return

        conn = None
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                rules_data = json.load(f)

            if not rules_data:
                return

            conn = self.get_connection()
            with conn.cursor() as cursor:
                # 1. Clear existing rules to avoid duplicates
                log.info("Syncing rules: Clearing old rules from DB...")
                cursor.execute("TRUNCATE TABLE team_rules")

                # 2. Insert new rules from JSON
                sql = "INSERT INTO team_rules (category, rule_content, severity) VALUES (%s, %s, %s)"
                values = []
                for r in rules_data:
                    values.append((r['category'], r['rule_content'], r['severity']))
                
                cursor.executemany(sql, values)
            
            conn.commit()
            log.info(f"Successfully synced {len(values)} rules from JSON to MySQL.")

        except Exception as e:
            log.error(f"Failed to sync rules from JSON: {e}")
        finally:
            if conn:
                conn.close()

    def get_active_rules(self):
        conn = None
        rules_text = ""
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM team_rules WHERE is_active = 1")
                rules = cursor.fetchall()
                
                if not rules:
                    return "No specific team rules configured."
                
                for i, r in enumerate(rules):
                    rules_text += f"{i+1}. [{r['category'].upper()}] {r['rule_content']} (Severity: {r['severity']})\n"
        except Exception as e:
            log.error(f"Failed to fetch rules: {e}")
            return "Error fetching team rules."
        finally:
            if conn:
                conn.close()
        return rules_text

    def save_review_record(self, file_path, verdict, report_content):
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cursor:
                sql = "INSERT INTO review_history (file_path, verdict, ai_report) VALUES (%s, %s, %s)"
                cursor.execute(sql, (file_path, verdict, report_content))
            conn.commit()
            log.info(f"Review history saved to DB (Verdict: {verdict})")
        except Exception as e:
            log.error(f"Failed to save review history: {e}")
        finally:
            if conn:
                conn.close()

db = DBManager()
# -*- coding: utf-8 -*-
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

host = os.getenv("POSTGRESQL_HOST")
port = os.getenv("POSTGRESQL_PORT", "5432")
user = os.getenv("POSTGRESQL_USER")
password = os.getenv("POSTGRESQL_PASSWORD")
db = os.getenv("POSTGRESQL_DB")

conn_str = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
engine = create_engine(conn_str, pool_size=30, pool_recycle=3600, pool_timeout=15)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


ALTER SYSTEM SET timezone = 'Europe/Moscow';

-- Create the frigate database
CREATE DATABASE frigate;
ALTER DATABASE frigate SET timezone = 'Europe/Moscow';

-- Create the frigate user with password
CREATE USER frigate WITH PASSWORD 'frigate';

-- Grant all privileges on frigate database to both users
GRANT ALL PRIVILEGES ON DATABASE frigate TO frigate;
GRANT ALL PRIVILEGES ON DATABASE frigate TO rgzz;

-- Connect to frigate database to set up permissions for future tables
\c frigate

-- Grant schema privileges to both users
GRANT ALL ON SCHEMA public TO frigate;
GRANT ALL ON SCHEMA public TO rgzz;

-- Make sure future tables will be accessible
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO frigate;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rgzz;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO frigate;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO rgzz;

from hdbcli import dbapi
from sqlalchemy import create_engine
import pandas as pd
import sys

def create_con(db_cons:pd.DataFrame, db:str):
    '''
    This function establishes a connection to a database

    Args:
        db_cons: The dataframe that describes all of your database connection information
        db: the name of the database that you would like to establish a connection to

    Returns:
        A connection engine that can be used to query data from a database
    '''

    # access the data for the database you wish you to use
    info_df = db_cons[db_cons['database'] == db].iloc[0]

    # obtain required information to establish database connection
    HOST = info_df['host_name']
    DB_NAME = info_df['db_name']
    TYPE = info_df['type']
    PORT = info_df['port']
    USER = info_df['user']
    PASSWORD = info_df['password']

    if TYPE == 'HANA':
        # check to see if the user has an unupdated db_connections file
        if USER == '[insert your username]' or PASSWORD == '[insert your password]':
            sys.exit("Error: The db_connections.json file has unupdated connection.")
            sys.exit(1)
        
        # create the connection
        try:
            conn = dbapi.connect(
                address=HOST,
                port=PORT,
                user=USER,
                password=PASSWORD
            )
        except:
            sys.exit("Error: Login did not work :(")
            sys.exit(1)
    elif TYPE == 'POSTGRES':
        connection_string = f"postgresql://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB_NAME}"

        conn = create_engine(connection_string)

        try:
            with conn.connect() as connection:
                print("Successfully connected to the PostgreSQL database!")
        except Exception as e:
            print(f"Connection failed: {e}")

    return (conn, TYPE)

def read_query(query:str, con) -> pd.DataFrame:
    engine, db_type = con

    if db_type == 'HANA':
        # execute query using db connection
        cursor = engine.cursor()
        cursor.execute(query)

        # obtain the columns from the table
        cols = [desc[0] for desc in cursor.description]

        # obtain the row information for the table
        results = cursor.fetchall()

        # transform results into a pandas dataframe
        df = pd.DataFrame(results, columns=cols)

        # close established connection
        # NOTE: this will not make the engine unusable in the future
        cursor.close()
    elif db_type == 'POSTGRES':
        try:
            df = pd.read_sql_query(query, con=engine)
        except Exception as e:
            print(f"An error occurred: {e}")

    return df

def basic_query(query_name:str, con) -> pd.DataFrame:
    '''
    Used to execute a query that does not need any form of special processing
    (i.e. overriding reformatting values, running in parallel/series, etc.)

    Args:
        query_name: the string that lists the path to the sql file to run
        db_con: the connection engine to the database that will be used to execute the query

    Returns:
        A dataframe that contains the result of the sql query
    '''

    # read query into file
    f = open(query_name)
    sql_query = f.read()
    f.close()

    # execute query and return
    return read_query(query=sql_query, con=con)
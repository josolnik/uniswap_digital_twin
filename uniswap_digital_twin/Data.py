import requests
import json
import pandas as pd
from tqdm import tqdm
import numpy as np
from datetime import datetime
from typing import List
from pandas import DataFrame

######Global Params#######
graph_url = 'https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v2'

col_data_types = {'amount0': float, 'amount1': float, 'logIndex': int, 'liquidity': float,
                  'amount0In': float, 'amount0Out': float, 'amount1In': float, 'amount1Out': float}
#########################



def process_query(query: str, data_field: str, graph_url: str) -> List[dict]:
    """
    Helper function to take a query and retrieve the data.
    query (str): The query to be executed
    data_field (str): The data field to be pulled out
    graph_url (str): The url of the subgraph
    """
    
    #Make the request
    request = requests.post(graph_url, json={'query': query})
    
    #Pull the json out from the text
    data = json.loads(request.text)
    
    #Pull out the relevant data field
    data = data['data'][data_field]
    
    return data

def convert_where_clause(clause: dict) -> str:
    """
    Convert a dictionary of clauses to a string for use in a query

    Parameters
    ----------
    clause : dict
        Dictionary of clauses

    Returns
    -------
    str
        A string representation of the clauses

    """
    out = "{"
    for key in clause.keys():
        out += "{}: ".format(key)
        out += '"{}"'.format(clause[key])
        out += ","
    out += "}"
    return out

def query_builder(main: str, fields: List[str], first: int = 100,
                  skip: int = None, order_by: str = None,
                  order_direction: str = None,
                 where_clause: dict = None) -> str:
    """
    Function for creation of a query string.

    Parameters
    ----------
    main : str
        The query to be run
    fields : List[str]
        The fields to pull in the query
    first : int, optional
        The number of records to pull
    skip : int, optional
        The number of records to skip
    order_by : str, optional
        The field to order by
    order_direction : str, optional
        The direction to order by
    where_clause : dict, optional
        A dictionary of clauses for filtering of the records

    Returns
    -------
    str
        A query string constructed from all the parameters.

    """

    #Assert the correct values for first and skip are used
    assert first >= 1 and first <= 1000, "The value for first must be within 1-1000"
    if skip:
        assert skip >= 0 and skip <= 5000, "The value for skip must be within 1-5000"
    
    #List of main parameters
    main_params = []
    main_params.append("first: {}".format(first))
    if skip:
        main_params.append("skip: {}".format(skip))
    if order_by:
        main_params.append("orderBy: {}".format(order_by))
    if order_direction:
        main_params.append("orderDirection: {}".format(order_direction))
    if where_clause:
        #Convert where clause
        where_clause = convert_where_clause(where_clause)
        main_params.append("where: {}".format(where_clause))
        
    #Convert params to a string
    main_params = ", ".join(main_params)
    
    #Convert fields to a string
    fields = ", ".join(fields)
    
    #Add in all the components
    query = """query{{
    {}({}){{
    {}
    }}
    }}""".format(main, main_params, fields)
    
    return query

def pull_data(query_function: PaginatedQuery) -> DataFrame:
    """
    Function to pull query data then process

    Parameters
    ----------
    query_function : PaginatedQuery
        The paginated query object that retrieves our data

    Returns
    -------
    DataFrame
        A dataframe with the data pulled from our query

    """
    
    #Pull the data
    data = query_function.run_queries()
    data['timestamp'] = pd.to_datetime(data['timestamp'], unit = 's')
    data['event'] = query_function.data_field
    
    #Create mapping of column data types
    cdt = {}
    #Check each column
    for col in data.columns:
        #If it has a mapping add it to cdt
        if col in col_data_types.keys():
            cdt[col] = col_data_types[col]
            
    #Map the data types
    data = data.astype(cdt)
    
    return data



def find_data_overlap(data):
    """
    Function to find the earliest date that ensures data overlap.
    """
    return max([df['timestamp'].min() for df in data])

def process_amount(df):
    if df['event'].iloc[0] == 'mints':
        pass
    elif df['event'].iloc[0] == 'burns':
        df[['amount0', 'amount1', 'liquidity']] *= -1
    elif df['event'].iloc[0] == 'swaps':
        df['amount0'] = df['amount0In'] - df['amount0Out']
        df['amount1'] = df['amount1In'] - df['amount1Out']
        df['liquidity'] = 0
        df.drop(columns=['amount0Out', 'amount0In', 'amount1Out', 'amount1In'], inplace=True)
        
def process_events(df):
    if df['event'].iloc[0] == 'mints':
        df['event'] = 'mint'
    elif df['event'].iloc[0] == 'burns':
        df['event'] = 'burn'
    elif df['event'].iloc[0] == 'swaps':
        df['event'] = (df['amount0'] > 0).map({True: 'ethPurchase', False: 'tokenPurchase'})

def process_data(data, lim_date=False):
    #Do all data processing
    for df in data:
        process_amount(df)
        process_events(df)
    
    #Consider only overlapping data
    if lim_date:
        overlap_date = find_data_overlap(data)
        data = [df[df['timestamp'] >= overlap_date] for df in data]
    
    #Concat
    data = pd.concat(data)
    
    #Drop the id column
    data = data.drop(columns=['id'])
    
    #Rename columns
    data = data.rename(columns={'amount0': 'token_delta', 'amount1': 'eth_delta', 'liquidity': 'UNI_delta'})
    
    #Indexing
    data = data.sort_values(['timestamp', 'logIndex'])
    data.reset_index(inplace = True, drop = True)
    
    #Find balances over time
    for col1, col2 in zip(['token_balance', 'eth_balance', 'UNI_supply'], ['token_delta', 'eth_delta', 'UNI_delta']):
        data[col1] = data[col2].cumsum()
    
    return data

def add_starting_state(data):
    #Find the minimum date
    start_date = data['timestamp'].min()

    #Truncate to hour
    start_date = datetime(start_date.year, start_date.month, start_date.day, start_date.hour)
    
    #Convert to unix timestamp
    unix_ts = int((start_date - datetime(1970,1,1)).total_seconds() )
    
    #Add an hour ahead to reflect that data is end of the hour marked
    start_date = start_date + pd.Timedelta("1h")

    #Clip out anything before the start date
    data = data[data['timestamp'] >= start_date].copy()
    

    #Build query
    query = """query{{
      pairHourDatas (where: {{pair: "0x8ae720a71622e824f576b4a8c03031066548a3b1", hourStartUnix: {} }}){{
        reserve0,
        reserve1,
        hourStartUnix
      }}
    }}
    """.format(unix_ts)

    #Pull the starting state
    start_state = process_query(query, "pairHourDatas", graph_url)

    #Check to make sure only one has been pulled down and it equals the unix_ts
    assert len(start_state) == 1, "Start state length not equal to 1"
    start_state = start_state[0]
    assert start_state['hourStartUnix'] == unix_ts, "The timestamps do not match"

    #Convert and find liquidity
    start_state['reserve0'] = float(start_state['reserve0'])
    start_state['reserve1'] = float(start_state['reserve1'])
    start_state['liquidity'] = (start_state['reserve0'] * start_state['reserve1']) ** 0.5

    #Convert start state to correct format
    start_state = {'token_delta': start_state['reserve0'],
     'eth_delta': start_state['reserve1'],
     'UNI_delta': start_state['liquidity'],
     'logIndex': np.NaN,
     'timestamp': start_date,
     'event': np.NaN,
     'token_balance': start_state['reserve0'],
     'eth_balance': start_state['reserve1'],
     'UNI_supply': start_state['liquidity']}

    #Append start state
    data = data.append(start_state, ignore_index=True)

    #Sort and reset index
    data = data.sort_values(['timestamp', 'logIndex'])
    data = data.reset_index(drop=True)
    
    #Find balances over time
    for col1, col2 in zip(['token_balance', 'eth_balance', 'UNI_supply'], ['token_delta', 'eth_delta', 'UNI_delta']):
        data[col1] = data[col2].cumsum()
    
    return data

def convert_to_unix(dt: datetime) -> int:
    """
    Convert a datetime to a unix number

    Parameters
    ----------
    dt : datetime
        The datetime to convert

    Returns
    -------
    int
        An integer representing the datetime in unix

    """
    return int((dt - datetime(1970,1,1)).total_seconds() )

class PaginatedQuery:
    """
    A class which handles a paginated query. Attributes of the base query are specified
    and then given the latest ID, there is an update to the query. The sorting must be
    done on the ID to ensure no data is missed.
    """
    
    def __init__(self, main: str, fields: List[str], data_field: str,
                  where_clause: dict = None, first: int = None,
                  start_date: datetime = None, end_date: datetime = None) -> None:
        """
        

        Parameters
        ----------
        main : str
            The main query that is being run.
        fields : List[str]
            A list of strings representing each field we want to pull.
        data_field : str
            The data field to pull out of the json
        where_clause : dict, optional
            A dictionary of clauses for filtering with the where statement
        first : int, optional
            Number of records to grab (maximum 1000)
        start_date : datetime, optional
            The start date of the data
        end_date : datetime, optional
            The end date of the data

        Returns
        -------
        None

        """
                
        self.main = main
        self.fields = fields
        self.data_field = data_field
        #If there is no where clause, convert it to an empty dictionary
        if where_clause is None:
            where_clause = {}
        self.where_clause = where_clause
        self.first = first
        self.start_date = start_date
        self.end_date = end_date
        
        #Convert the dates to unix and add them to the where clause
        if self.start_date:
            start_date_unix = convert_to_unix(start_date)
            self.where_clause['timestamp_gte'] = start_date_unix
        if self.end_date:
            end_date_unix = convert_to_unix(end_date+pd.Timedelta("1D")) - 1
            self.where_clause['timestamp_lte'] = end_date_unix
                
    def run_queries(self) -> DataFrame:
        """
        

        Returns
        -------
        DataFrame
            Returns a pandas dataframe filled with the data from queries

        """
        
        #For tracking the data
        output = []
        
        #For tracking the last minimum index
        last_min_index = None
        
        #Copy the where clause
        where_clause = self.where_clause.copy()
            
        while True:
            #Add in the minimum index
            if last_min_index:
                where_clause['id_lt'] = last_min_index
            
            #Build the query
            query = query_builder(self.main, self.fields,
                         first=self.first, order_by="id", order_direction="desc",
                                 where_clause=where_clause)
            
            #Pull the data
            data = process_query(query, self.data_field, graph_url)

            #Convert to a pandas dataframe
            data = pd.DataFrame(data)

            #If length of data is 0 return
            if len(data) == 0:
                #If no output return none, otherwise concat
                if len(output) == 0:
                    return None
                else:
                    return pd.concat(output)

            #Get the latest minimum index
            last_min_index = data['id'].min()

            #Append the data
            output.append(data)

def create_data(start_date=None, end_date=None):
    #Build queries for mint, burn, swap
    mint_query = PaginatedQuery("mints",
                            ["id","timestamp", "amount0", "amount1", "logIndex", "liquidity"], "mints",
                     first=1000, where_clause={"pair": "0x8ae720a71622e824f576b4a8c03031066548a3b1"},
                           start_date = start_date,
                           end_date = end_date)
    
    burns_query = PaginatedQuery("burns",
                            ["id", "timestamp", "amount0", "amount1", "logIndex", "liquidity"], "burns",
                     first=1000, where_clause={"pair": "0x8ae720a71622e824f576b4a8c03031066548a3b1"},
                           start_date = start_date,
                           end_date = end_date)
    
    swaps_query = PaginatedQuery("swaps",
                            ["id","timestamp", "amount0In", "amount1In", "amount0Out", "amount1Out","logIndex"], "swaps",
                     first=1000, where_clause={"pair": "0x8ae720a71622e824f576b4a8c03031066548a3b1"},
                           start_date = start_date,
                           end_date = end_date)
    
    #Pull and process data
    queries = [mint_query, burns_query, swaps_query]
    data = [pull_data(q) for q in queries]
    data = process_data(data, lim_date=True)
    
    #Add in starting state
    data = add_starting_state(data)
    return data



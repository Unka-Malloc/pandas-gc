import pysdql

if __name__ == '__main__':
    unoptimized = pysdql.tpch_query(range(1, 23), verbose=False, optimize=True)
import pandas as pd
from pysdql import tosdql

if __name__ == '__main__':
    li = pd.read_csv(f"/home/cxunka/Dataset/tpch_dataset/100M/lineitem.tbl",
                     sep='|',
                     header=None,
                     names=['l_orderkey', 'l_partkey', 'l_suppkey', 'l_linenumber', 'l_quantity',
                            'l_extendedprice', 'l_discount', 'l_tax', 'l_returnflag', 'l_linestatus',
                            'l_shipdate', 'l_commitdate', 'l_receiptdate', 'l_shipinstruct', 'l_shipmode', 'l_comment'],
                     index_col=False,
                     dtype={"l_orderkey": int, "l_partkey": int, "l_suppkey": int, "l_linenumber": int,
                            "l_quantity": float, "l_extendedprice": float, "l_discount": float, "l_tax": float,
                            "l_returnflag": str, "l_linestatus": str, "l_shipinstruct": str, "l_shipmode": str,
                            "l_comment": str},
                     parse_dates=['l_shipdate', 'l_commitdate', 'l_receiptdate'])


#     @tosdql
#     def q_a(lineitem):
#         result = lineitem[['l_orderkey', 'l_suppkey']]
#         return result

#     df1 = q_a(li)

    print(li)

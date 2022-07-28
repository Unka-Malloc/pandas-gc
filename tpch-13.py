"""
select
	c_count,
	count(*) as custdist
from
	(
		select
			c_custkey,
			count(o_orderkey)
		from
			customer left outer join orders on
				c_custkey = o_custkey
				and o_comment not like '%:1%:2%'
		group by
			c_custkey
	) as c_orders (c_custkey, c_count)
group by
	c_count
"""
import pysdql

if __name__ == '__main__':
    var1 = 'special'
    var2 = 'requests'

    orders = pysdql.read_tbl(path=r'T:/UG4-Proj/datasets/orders.tbl', header=pysdql.ORDERS_COLS)
    customer = pysdql.read_tbl(path=r'T:/UG4-Proj/datasets/customer.tbl', header=pysdql.CUSTOMER_COLS)

    sub_o = orders[~orders['o_comment'].contains_in_order(var1, var2)].rename('sub_o')

    # LEFT OUTER JOIN
    r = customer.join(sub_o, how='left', left_on='c_custkey', right_on='o_custkey')

    r = r.groupby(['c_custkey']).agg(c_count=(r['o_orderkey'], 'count'))

    c_orders = r.rename('c_orders')

    s = c_orders.groupby(['c_count']).agg(custdist=('*', 'count'))

    pysdql.db_driver(db_path=r'T:/sdql', name='tpch-13').run(s, block=False).export().to()

    # run interpret progs/tpch/q13_unfused.sdql

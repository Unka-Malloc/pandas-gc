import copy
import inspect
import os
import pathlib
import re
import string

from varname import varname

from pysdql.const import (
    CUSTOMER_COLS,
    LINEITEM_COLS,
    ORDERS_COLS,
    NATION_COLS,
    REGION_COLS,
    PART_COLS,
    SUPPLIER_COLS,
    PARTSUPP_COLS
)
from pysdql.core.exprs.advanced.AggrOpExprs import AggrOpRename, AggrBinOp

from pysdql.core.interfaces.api import (
    Replaceable,
    Retrivable,
)

from pysdql.core.enums.EnumUtil import (
    LogicSymbol,
    OptGoal,
    AggrType,
    OpRetType,
    PandasRetType,
)

from pysdql.core.dtypes.DataFrameGroupBy import DataFrameGroupBy

from pysdql.core.exprs.basic.ColEl import ColEl
from pysdql.core.exprs.basic.IterEl import IterEl

from pysdql.core.exprs.advanced.MergeExprs import (
    MergeExpr,
    MergeIndicator,
)
from pysdql.core.exprs.advanced.ColAlterExprs import (
    OldColRename,
    NewColInsert,
    NewColListInsert,
)
from pysdql.core.exprs.advanced.ColOpExprs import (
    ColOpBinary,
    ColOpApply,
    ColOpExternal,
)
from pysdql.core.exprs.advanced.ColProjExprs import (
    ColProjUnique,
    ColProjExtra,
    ColProjRename,
    ColProj
)
from pysdql.core.exprs.advanced.ColOpIsinExpr import ColOpIsin
from pysdql.core.exprs.advanced.ColBridgeExprs import (
    ColElBridge,
    ColOpBridge,
)
from pysdql.core.exprs.advanced.BinCondExpr import BinCondExpr

from pysdql.core.exprs.complex.AggrExpr import AggrExpr
from pysdql.core.exprs.complex.GroupbyAggrExpr import GroupbyAggrExpr
from pysdql.core.exprs.complex.FlexChain import OpChain

from pysdql.core.exprs.carrier.StructExpr import DataFrameStruct
from pysdql.core.exprs.carrier.OpExpr import OpExpr
from pysdql.core.exprs.carrier.TransExpr import TransExpr
from pysdql.core.exprs.carrier.VarBindExpr import VarBindExpr

from pysdql.core.exprs.collection.OpSeq import OpSeq
from pysdql.core.exprs.collection.VarBindSeq import VarBindSeq

from pysdql.core.reasoner.AggrFrame import AggrFrame
from pysdql.core.reasoner.GroupbyAggrFrame import GroupbyAggrFrame
from pysdql.core.reasoner.HashBuildRelay import BuildEnd
from pysdql.core.reasoner.Optimizer import Optimizer

from pysdql.core.killer.Retriever import Retriever

from pysdql.core.prototype.basic.sdql_ir import *

from pysdql.extlib.sdqlir_to_sdqlpy import GenerateSDQLPYCode

class DataFrame(Replaceable, Retrivable):
    def __init__(self,
                 data=None,
                 index=None,
                 columns=None,
                 dtype=None,
                 name=None,
                 operations=None,
                 is_joint=False,
                 is_original=True,
                 context_variable=None,
                 context_constant=None,
                 loader=None,
                 previous_name=None,
                 dataset_name=None):
        super().__init__()

        # data=None, index=None, columns=None, dtype=None, copy=None

        self.loader = loader
        self.__default_name = 'R'
        self.__data = data
        self.__index = index
        self.__dtype = dtype
        self.__name = name if name else varname()
        self.__columns = copy.copy(columns) if columns else self.preset_cols()
        self.__columns_in = copy.copy(columns) if columns else self.preset_cols()
        self.__operations = operations if operations else OpSeq()
        self.__retriever = Retriever(self)
        self.__previous_name = previous_name if previous_name else ""
        self.__dataset_name = dataset_name if dataset_name else ""

        tmp_useful_name = self.__name if self.__name else self.__previous_name

        self.__structure = DataFrameStruct('1DT')

        self.__iter_el = IterEl(f'x_{tmp_useful_name}')
        self.__var_expr = self.init_var_expr()

        self.__is_merged = is_joint

        self.context_constant = {}

        self.context_variable = context_variable if context_variable else {}
        self.context_constant = context_constant if context_constant else {}

        self.init_context_variable()

        if is_joint:
            vname_part = f'{self.get_name()}'
            self.__var_merge_part = VarExpr(vname_part)
            self.add_context_variable(vname_part,
                                      self.__var_merge_part)
        else:
            vname_part = f'{tmp_useful_name}_part'
            self.__var_merge_part = VarExpr(vname_part)
            self.add_context_variable(vname_part,
                                      self.__var_merge_part)

        vname_aggr = f'{tmp_useful_name}_aggr'
        self.__var_aggr = VarExpr(vname_aggr)

        self.unopt_count = 0
        self.unopt_vars = {}
        self.unopt_consts = {}

        self.transform = TransExpr(self)

        self.original = is_original
        self.__is_original = is_original

        self.copy_cache = None

    @property
    def is_original(self):
        return self.__is_original

    @property
    def dtypes(self):
        return self.__dtype

    def copy(self):
        next_name = varname()

        next_df = DataFrame(data=copy.copy(self.data),
                            index=copy.copy(self.index),
                            columns=copy.copy(self.columns),
                            dtype=copy.copy(self.dtypes),
                            name=next_name,
                            operations=copy.copy(self.operations),
                            is_joint=copy.copy(self.is_joint),
                            is_original=False,
                            context_variable=copy.copy(self.context_variable),
                            context_constant=copy.copy(self.context_constant),
                            loader=copy.deepcopy(self.loader),
                            previous_name=copy.copy(self.current_name),
                            dataset_name=copy.copy(self.dataset_name))

        return next_df

    @property
    def is_joint(self):
        return self.__is_merged

    @property
    def is_merged(self):
        return self.__is_merged

    def add_const(self, const):
        if type(const) == str:
            if const not in self.context_constant.keys():
                tmp_vname = (''.join(re.split(r'[^A-Za-z]', const))).lower() + ''.join(
                    [i for i in const if i.isdigit()])
                if tmp_vname.isdigit():
                    tmp_vname = f'v{tmp_vname}'
                tmp_var = VarExpr(tmp_vname)
                self.context_constant[const] = tmp_var
                self.context_variable[tmp_vname] = tmp_var
        else:
            raise ValueError

    def get_const_var(self, const):
        return self.context_constant[const]

    def pre_def_var_const(self):
        pass

    @staticmethod
    def map_name(name):
        if name in ['cu', 'customer']:
            return 'customer'
        if name in ['li', 'lineitem']:
            return 'lineitem'
        if name in ['ord', 'orders']:
            return 'orders'
        if name in ['pa', 'part']:
            return 'part'
        if name in ['su', 'supplier']:
            return 'supplier'
        if name in ['ps', 'partsupp']:
            return 'partsupp'
        if name in ['na', 'nation']:
            return 'nation'
        if name in ['re', 'region']:
            return 'region'

    def init_var_expr(self):
        if self.name == 'customer':
            return VarExpr("db->cu_dataset")
        if self.name == 'lineitem':
            return VarExpr("db->li_dataset")
        if self.name == 'orders':
            return VarExpr("db->ord_dataset")
        if self.name == 'nation':
            return VarExpr('db->na_dataset')
        if self.name == 'region':
            return VarExpr('db->re_dataset')
        if self.name == 'part':
            return VarExpr("db->pa_dataset")
        if self.name == 'supplier':
            return VarExpr('db->su_dataset')
        if self.name == 'partsupp':
            return VarExpr('db->ps_dataset')

        return VarExpr(self.name)

    @property
    def var_expr(self):
        return self.__var_expr

    @property
    def data(self):
        return self.__data
        # if self.columns:
        #     columns_names = self.columns
        # else:
        #     columns_names = list(self.__data.keys())
        #
        # data_size = len(self.__data[columns_names[0]])
        #
        # rec_dict = {}
        # for i in range(data_size):
        #     tmp_dict = {}
        #     for k in columns_names:
        #         tmp_dict[k] = self.__data[k][i]
        #     rec_dict[RecEl(tmp_dict)] = 1
        # return DictEl(rec_dict)

    @property
    def index(self):
        return self.__index

    '''
    Columns
    Columns In
    Columns Out
    Columns Used
    '''

    def preset_cols(self) -> list:
        if self.__name in ['customer', 'cu']:
            return CUSTOMER_COLS
        if self.__name in ['lineitem', 'li']:
            return LINEITEM_COLS
        if self.__name in ['orders', 'ord']:
            return ORDERS_COLS
        if self.__name in ['nation', 'na']:
            return NATION_COLS
        if self.__name in ['region', 're']:
            return REGION_COLS
        if self.__name in ['part', 'pa']:
            return PART_COLS
        if self.__name in ['supplier', 'su']:
            return SUPPLIER_COLS
        if self.__name in ['partsupp', 'ps']:
            return PARTSUPP_COLS
        return []

    @property
    def columns(self) -> list:
        return self.__columns

    @property
    def cols_in(self) -> list:
        return self.__columns_in

    @property
    def cols_out(self) -> list:
        return self.infer_cols_out()

    def infer_cols_out(self) -> list:
        """
        What could change columns?
        The last operation:
            1. col proj
            2. groupby agg
            3. agg
            4. merge
        :return:
        """
        tmp_cols = []

        rename_cols = {}

        for op_expr in self.operations:
            op_body = op_expr.op

            if isinstance(op_body, NewColInsert):
                tmp_cols.append(op_body.col_var)
            elif isinstance(op_body, OldColRename):
                if isinstance(op_body.col_expr, str):
                    rename_cols[op_body.col_var] = op_body.col_expr
                elif isinstance(op_body.col_expr, ColOpApply):
                    pass
                else:
                    raise NotImplementedError(f'Unexpected type: {type(op_body.col_expr)}')
            elif isinstance(op_body, ColProj):
                tmp_cols = copy.copy(op_body.proj_cols)
            elif isinstance(op_body, AggrExpr):
                tmp_cols = [i for i in list(op_body.aggr_op.keys())]
            elif isinstance(op_body, GroupbyAggrExpr):
                tmp_cols = [i for i in (op_body.groupby_cols + list(op_body.aggr_dict.keys()))]
            elif isinstance(op_body, MergeExpr):
                if self.name == op_body.joint.name:
                    tmp_cols = [i for i in (op_body.left.cols_out + op_body.right.cols_out)]
        else:
            if tmp_cols:
                for k in rename_cols.keys():
                    if k in tmp_cols:
                        tmp_cols[tmp_cols.index(k)] = rename_cols[k]
                    else:
                        raise IndexError(f'{k} not found in {self.name} columns {tmp_cols}')

                # non_dup_cols = []
                # for i in tmp_cols:
                #     if i not in non_dup_cols:
                #         non_dup_cols.append(i)
                return tmp_cols
            else:
                return self.cols_in

    @property
    def cols_used(self):
        return self.retriever.findall_cols_used(as_owner=True)

    @property
    def dataset_name(self):
        return self.__dataset_name

    @property
    def previous_name(self):
        return self.__previous_name

    @property
    def current_name(self):
        return self.__name

    @property
    def name(self):
        if self.__previous_name:
            return self.__previous_name
        if self.__name:
            return self.__name
        return self.__default_name

    def get_name(self):
        if self.__previous_name:
            return self.__previous_name
        if self.__name:
            return self.__name
        return self.__default_name

    def get_var_part(self):
        return self.__var_merge_part

    @property
    def var_part(self):
        return self.__var_merge_part

    @property
    def var_build(self):
        return self.__var_merge_part

    def get_var_aggr(self):
        return self.__var_aggr

    @property
    def var_aggr(self):
        return self.__var_aggr

    @property
    def tmp_name_list(self):
        return ['tmp_a', 'tmp_b', 'tmp_c', 'tmp_d', 'tmp_e', 'tmp_f', 'tmp_g',
                'tmp_h', 'tmp_i', 'tmp_j', 'tmp_k', 'tmp_l', 'tmp_m', 'tmp_n',
                'tmp_o', 'tmp_p', 'tmp_q', 'tmp_r', 'tmp_s', 'tmp_t',
                'tmp_u', 'tmp_v', 'tmp_w', 'tmp_x', 'tmp_y', 'tmp_z']

    @staticmethod
    def hard_code_tmp_name():
        name_list = []
        for i in list(string.ascii_lowercase):
            name_list.append(f'tmp_{i}')

    def gen_tmp_name(self, noname=None):
        if noname is None:
            noname = [self.name] + self.history_name

        for tmp_name in self.tmp_name_list:
            if tmp_name not in noname:
                return tmp_name
        else:
            for i in range(1024):
                tmp_name = f'tmp_{i}'
                if tmp_name not in noname:
                    return tmp_name
            else:
                raise ValueError('Failed to generate tmp name!')

    @property
    def operations(self):
        return self.__operations

    @property
    def op_stack(self):
        return self.__operations

    @property
    def history_name(self):
        return self.operations.names

    def pop(self):
        self.operations.pop()

    def push(self, val):
        self.operations.push(val)

    @property
    def mutable(self):
        if self.__data:
            return False
        return True

    @property
    def iter_el(self) -> IterEl:
        return self.__iter_el

    @property
    def iter_key(self):
        return self.iter_el.key

    @property
    def el(self):
        return self.iter_el

    def key_access(self, field):
        if self.is_joint:
            if field in self.partition_side.columns:
                return self.partition_side.key_access(field)
            elif field in self.probe_side.columns:
                return self.probe_side.key_access(field)
        return RecAccessExpr(self.iter_el.key, field)

    def val_access(self, field):
        return RecAccessExpr(self.iter_el.value, field)

    def optimize(self) -> str:
        opt = self.get_opt()
        for op_expr in self.operations:
            opt.input(op_expr)
        query = opt.output

        last_list = [self.define_variables(),
                     f'query = {self.define_constants().get_sdql_ir(query)}']

        return '\n'.join(last_list)

    def opt_to_sdqlir(self, indent='    ', verbose=True) -> str:
        opt = Optimizer(opt_on=self,
                        opt_goal=OptGoal.Infer)
        for op_expr in self.operations:
            opt.input(op_expr)
        query_obj = opt.output

        query_str = GenerateSDQLPYCode(self.define_constants().get_sdql_ir(query_obj), {})

        query_list = query_str.split('\n')

        query_list = query_list[:query_list.index('True')]

        if verbose:
            print('>> Optimized Query <<')

            print(f'{"=" * 60}')

            print('\n'.join(query_list))

            print(f'{"=" * 60}')

        query_list = [f'{indent}{i}' for i in query_list]

        return '\n'.join(query_list)

    '''
    FlexIR
    '''

    @property
    def replaceable(self):
        return False

    @property
    def oid(self):
        return hash((self.name))

    @property
    def sdql_ir(self):
        opt = self.get_opt()
        for op_expr in self.operations:
            opt.input(op_expr)
        return opt.output

    @property
    def expr(self) -> str:
        if self.name:
            return self.name
        return self.data.expr

    def __repr__(self):
        return self.expr

    @property
    def sdql_expr(self):
        return

    def __str__(self):
        return self.name

    @property
    def structure(self) -> str:
        return self.__structure.type

    @structure.setter
    def structure(self, val: str):
        self.__structure = DataFrameStruct(val)

    def __getitem__(self, item):
        if type(item) == str:
            # next_df = self.copy_cache if self.copy_cache else self.create_copy(location=f'__getitem__({item})')
            return self.get_col(col_name=item)

        if type(item) == CompareExpr:
            return self[BinCondExpr(unit1=item.leftExpr,
                                    operator=item.compareType,
                                    unit2=item.rightExpr)]
        if type(item) == MulExpr:
            return self[BinCondExpr(unit1=item.op1Expr,
                                    operator=LogicSymbol.AND,
                                    unit2=item.op2Expr)]
        if type(item) == AddExpr:
            return self[BinCondExpr(unit1=item.op1Expr,
                                    operator=LogicSymbol.OR,
                                    unit2=item.op2Expr)]

        if type(item) == BinCondExpr:
            next_df = self.create_copy(location='__getitem__(filter)')

            next_df.push(OpExpr(op_obj=item,
                             op_on=next_df,
                             op_iter=False))
            return next_df

        if type(item) == list:
            next_df = self.create_copy(location='__getitem__(projection)')
            next_df.push(OpExpr(op_obj=ColProj(next_df, item),
                             op_on=next_df,
                             op_iter=False))

            return next_df

        if type(item) == ColOpIsin:
            next_df = self.create_copy(location='__getitem__(isin)')
            # self.operations.push(OpExpr(op_obj=item,
            #                             op_on=self,
            #                             op_iter=True))

            return next_df

        if isinstance(item, ColOpExternal):
            if item.func in [ExtFuncSymbol.StringContains,
                             ExtFuncSymbol.StartsWith,
                             ExtFuncSymbol.EndsWith]:

                self.push(OpExpr(op_obj=item,
                                 op_on=self,
                                 op_iter=False))
                return self
            else:
                raise NotImplementedError(f'Unsupported external function {item.func}')

        if isinstance(item, MergeIndicator):
            next_df = self.create_copy(location=f'__getitem__(_merge_indicator)')
            self.operations.push(OpExpr(op_obj=item,
                                        op_on=self,
                                        op_iter=True))

            return next_df

        print(f'Warning: Unsupported __getitem__ {item}')

        next_df = self.create_copy(location=f'__getitem__({type(item)})')
        # self.operations.push(OpExpr(op_obj=item,
        #                             op_on=self,
        #                             op_iter=True))

        return next_df

    def __getattr__(self, item):
        if type(item) == str:
            return self.get_col(col_name=item)

    def get_col(self, col_name):
        """
        df['col_name'] = ?
        :param col_name:
        :return:
        """
        if col_name in self.columns:
            return ColEl(self, col_name)
        elif col_name in self.retriever.find_cols_used(mode='insert'):
            return ColEl(self, col_name)
        elif self.retriever.was_aggregated:
            if col_name in self.retriever.find_cols_used(mode='aggregation'):
                return ColEl(self, col_name)
        else:
            # print(self.operations)
            # return ColEl(self, col_name)
            print(f'Warning: Cannot find column "{col_name}" in {{ {self.name} }} -> {self.columns}')

            return ColEl(self, col_name)

    def __setitem__(self, key, value):
        # next_df = self.create_copy(location=f'__setitem__.({key})')

        if key in self.columns:
            if type(value) in (bool, int, float, str):
                return self.rename_col_scalar(key, value)
            if type(value) in (ColEl, ColOpBinary, ColOpExternal, ColOpApply):
                return self.rename_col_expr(key, value)
            if type(value) in (IfExpr,):
                return self.rename_col_expr(key, value)
        else:
            if type(value) in (bool, int, float, str):
                return self.insert_col_scalar(key, value)
            if type(value) in (ColEl, ColOpBinary, ColOpExternal, ColOpApply):
                return self.insert_col_expr(key, value)
            if type(value) in (IfExpr,):
                return self.insert_col_expr(key, value)
            if type(value) in (list,):
                self.push(OpExpr(op_obj=NewColListInsert(col_var=key,
                                                         col_list=value),
                                 op_on=self,
                                 op_iter=True))
                return self

    def rename(self, mapper: dict, axis=1, inplace=True):
        for key in mapper.keys():
            if key in self.columns:
                self.__columns[self.__columns.index(key)] = mapper[key]
            else:
                raise IndexError(f'Cannot find the column {key} in {self.name}')

            self.push(OpExpr(op_obj=OldColRename(col_var=key,
                                                 col_expr=mapper[key]),
                             op_on=self,
                             op_iter=False))

        return self

    def rename_col_scalar(self, key, value):
        raise NotImplementedError

    def rename_col_expr(self, key, value):
        self.push(OpExpr(op_obj=OldColRename(col_var=key,
                                             col_expr=value),
                         op_on=self,
                         op_iter=False))

    def insert_col_scalar(self, key, value):
        raise NotImplementedError

    def insert_col_expr(self, key, value):
        if isinstance(value, ColEl):
            if not self.retriever.equals(self, value.relation):
                self.push(OpExpr(op_obj=ColElBridge(col_from=value,
                                                    col_to=self.get_col(key)),
                                 op_on=self,
                                 op_iter=False))
            else:
                self.push(OpExpr(op_obj=NewColInsert(col_var=key,
                                                     col_expr=value),
                                 op_on=self,
                                 op_iter=False))
        elif isinstance(value, ColOpBinary):
            if not self.retriever.equals(self, value.relation):
                self.push(OpExpr(op_obj=ColOpBridge(col_from=value,
                                                    col_to=self.get_col(key)),
                                 op_on=self,
                                 op_iter=False))
            else:
                self.push(OpExpr(op_obj=NewColInsert(col_var=key,
                                                     col_expr=value),
                                 op_on=self,
                                 op_iter=False))
        else:
            self.push(OpExpr(op_obj=NewColInsert(col_var=key,
                                                 col_expr=value),
                             op_on=self,
                             op_iter=False))

    def groupby(self, cols, as_index=False, sort=False):
        next_df = self.create_copy(location='groupby')

        return DataFrameGroupBy(groupby_from=next_df,
                                groupby_cols=cols)

    @property
    def name_ops(self) -> str:
        output = self.name
        for op_expr in self.operations:
            output += op_expr.get_op_name_suffix()
        return output

    def merge(self, right, how='inner', left_on=None, right_on=None, sort=False, suffixes=('_x', '_y'), indicator=False, validate=None):
        if isinstance(right, ColEl):
            right.relation.push(OpExpr(op_obj=ColProjUnique(proj_on=right.relation,
                                                            proj_cols=[right.field]),
                                       op_on=right.relation,
                                       op_iter=False))

            return self.merge_with_dataframe(right=right.relation,
                                             how=how,
                                             left_on=left_on,
                                             right_on=right_on,
                                             sort=sort,
                                             suffixes=suffixes,
                                             indicator=indicator,
                                             validate=validate)

        if isinstance(right, DataFrame):
            return self.merge_with_dataframe(right=right,
                                             how=how,
                                             left_on=left_on,
                                             right_on=right_on,
                                             sort=sort,
                                             suffixes=suffixes,
                                             indicator=indicator,
                                             validate=validate)


    def merge_with_dataframe(self, right, how='inner', left_on=None, right_on=None, sort=False, suffixes=('_x', '_y'), indicator=False, validate=None):
        if isinstance(left_on, str) and isinstance(right_on, str):
            pass

        if isinstance(left_on, list) and isinstance(right_on, list):
            if len(left_on) != len(right_on):
                raise IndexError(f'Mismatch shape: left_on {left_on}, right_on {right_on}')

            if (isinstance(left_on, list) and len(left_on) == 1) and (isinstance(right_on, list) and len(right_on) == 1):
                left_on = left_on[0]
                right_on = right_on[0]

        next_left_df = self.create_copy(location=f'merge(left)')
        next_right_df = right.create_copy(location=f'merge(right)')

        next_context_variable = {}
        for k in next_left_df.context_variable.keys():
            next_context_variable[k] = next_left_df.context_variable[k]
        for k in next_right_df.context_variable.keys():
            next_context_variable[k] = next_right_df.context_variable[k]

        next_context_constant = {}
        for k in next_left_df.context_constant.keys():
            next_context_constant[k] = next_left_df.context_constant[k]
        for k in next_right_df.context_constant.keys():
            next_context_constant[k] = next_right_df.context_constant[k]

        next_name = f'{next_left_df.current_name}_{next_right_df.current_name}'

        next_cols = next_left_df.cols_out + next_right_df.cols_out

        tmp_df = DataFrame(name=next_name,
                           is_joint=True,
                           is_original=False,
                           columns=next_cols,
                           context_variable=next_context_variable,
                           context_constant=next_context_constant)

        merge_expr = MergeExpr(left=next_left_df,
                               right=next_right_df,
                               how=how,
                               left_on=left_on,
                               right_on=right_on,
                               joint=tmp_df)

        overlap_cols = list(set(next_left_df.cols_out).intersection(next_right_df.cols_out))

        all_cols_used = self.retriever.findall_cols_used(as_owner=False,
                                                         only_next=False,
                                                         exclude=None)

        used_overlap_cols = []

        for i in overlap_cols:
            if i in all_cols_used:
                used_overlap_cols.append(i)

        if used_overlap_cols:
            overlap_left_cols = [f'{i}{suffixes[0]}' if i in used_overlap_cols else i for i in next_left_df.cols_out]
            overlap_right_cols = [f'{i}{suffixes[1]}' if i in used_overlap_cols else i for i in next_right_df.cols_out]

            col_rename_proj_left = ColProjRename(base_merge=merge_expr,
                                                 from_left=copy.copy(next_left_df.cols_out),
                                                 to_left=overlap_left_cols,
                                                 from_right=copy.copy(next_right_df.cols_out),
                                                 to_right=overlap_right_cols,
                                                 is_left=True)

            next_left_df.push(OpExpr(op_obj=col_rename_proj_left,
                                     op_on=next_left_df,
                                     op_iter=True))

            col_rename_proj_right = ColProjRename(base_merge=merge_expr,
                                                  from_left=copy.copy(next_left_df.cols_out),
                                                  to_left=overlap_left_cols,
                                                  from_right=copy.copy(next_right_df.cols_out),
                                                  to_right=overlap_right_cols,
                                                  is_right=True)

            next_right_df.push(OpExpr(op_obj=col_rename_proj_right,
                                      op_on=next_right_df,
                                      op_iter=True))

            col_rename_proj_merge = ColProjRename(base_merge=merge_expr,
                                                  from_left=copy.copy(next_left_df.cols_out),
                                                  to_left=overlap_left_cols,
                                                  from_right=copy.copy(next_right_df.cols_out),
                                                  to_right=overlap_right_cols,
                                                  is_joint=True)

            tmp_df.push(OpExpr(op_obj=col_rename_proj_merge,
                               op_on=[next_left_df, next_right_df],
                               op_iter=True))

        next_left_df.push(OpExpr(op_obj=merge_expr,
                         op_on=[next_left_df, next_right_df],
                         op_iter=True))

        next_right_df.push(OpExpr(op_obj=merge_expr,
                          op_on=[next_left_df, next_right_df],
                          op_iter=True))

        tmp_df.push(OpExpr(op_obj=merge_expr,
                           op_on=[next_left_df, next_right_df],
                           op_iter=True))

        return tmp_df

    def get_op_chain(self):
        return OpChain(self)

    def get_opt(self, opt_goal=OptGoal.UnOptimized):
        opt = Optimizer(opt_on=self,
                        opt_goal=opt_goal)
        for op_expr in self.operations:
            opt.input(op_expr)
        return opt

    def agg(self, func=None, *agg_args, **agg_kwargs):
        if func:
            if type(func) == str:
                return self.agg_str_parse(func)
            if type(func) == dict:
                return self.agg_dict_parse(func)
        if agg_args:
            pass
        if agg_kwargs:
            return self.agg_kwargs_parse(agg_kwargs)

    def agg_str_parse(self, input_func):
        raise NotImplementedError

    def agg_dict_parse(self, input_aggr_dict):
        aggr_tuple_dict = {}
        for k in input_aggr_dict.keys():
            aggr_tuple_dict[k] = (k, input_aggr_dict[k])

        output_aggr_dict = {}

        for aggr_key in input_aggr_dict.keys():
            aggr_func = input_aggr_dict[aggr_key]

            if aggr_func == 'sum':
                output_aggr_dict[aggr_key] = self.key_access(aggr_key)
            if aggr_func == 'count':
                # i: int to float
                output_aggr_dict[aggr_key] = ConstantExpr(1.0)

        next_df = self.create_copy(location='agg')

        aggr_expr = AggrExpr(aggr_type=AggrType.Dict,
                             aggr_on=next_df,
                             aggr_op=output_aggr_dict,
                             aggr_else=ConstantExpr(None),
                             origin_dict=aggr_tuple_dict)

        op_expr = OpExpr(op_obj=aggr_expr,
                         op_on=next_df,
                         op_iter=True,
                         iter_on=next_df,
                         ret_type=OpRetType.DICT)

        next_df.push(op_expr)

        return next_df

    def agg_kwargs_parse(self, aggr_tuple_dict):
        agg_dict = {}

        for agg_key in aggr_tuple_dict.keys():
            agg_val = aggr_tuple_dict[agg_key]
            if not isinstance(agg_val, tuple):
                raise ValueError()

            agg_flag = aggr_tuple_dict[agg_key][1]

            if agg_flag == 'sum':
                agg_dict[agg_key] = self.key_access(agg_val[0])
            if agg_flag == 'count':
                # i: int to float
                agg_dict[agg_key] = ConstantExpr(1.0)
            if callable(agg_flag):
                # received lambda function
                # i: int to float
                agg_dict[agg_key] = ConstantExpr(1.0)

        next_df = self.create_copy(location='agg')

        aggr_expr = AggrExpr(aggr_type=AggrType.Dict,
                             aggr_on=next_df,
                             aggr_op=agg_dict,
                             aggr_else=ConstantExpr(None),
                             origin_dict=aggr_tuple_dict)

        op_expr = OpExpr(op_obj=aggr_expr,
                         op_on=next_df,
                         op_iter=True,
                         iter_on=next_df,
                         ret_type=OpRetType.DICT)

        next_df.push(op_expr)

        return next_df

    def peek(self):
        return self.operations.peek()

    def show_info(self):
        if self.is_joint:
            self.partition_side.show_info()
            self.probe_side.show_info()

        print(f'>> {self.name} Columns <<')
        print(self.columns)
        print(f'>> {self.name} Columns(In) <<')
        print(self.cols_in)
        print(f'>> {self.name} Columns(Out) <<')
        print(self.cols_out)
        print(f'>> {self.name} Columns(Used) <<')
        print(self.cols_used)
        if self.context_variable:
            print(f'>> {self.name} Context Variables <<')
            print(self.context_variable)
        if self.context_constant:
            print(f'>> {self.name} Context Constant <<')
            print(self.context_constant)
        print(f'>> {self.name} Operation Sequence <<')
        print(self.operations)
        print(f'========================================')

    def show(self):
        self.show_info()
        print(f'>> {self.name} Optimizer Output <<')
        print(self.optimize())
        # print(f'>> {self.name} Recursive Output <<')
        # print(self.unoptimize())
        print('>> Done <<')

    @property
    def partition_frame(self):
        return self.get_opt(OptGoal.JoinPartition).partition_frame

    @property
    def part_frame(self):
        return self.get_opt(OptGoal.JoinPartition).partition_frame

    def get_partition_frame(self):
        return self.get_opt(OptGoal.JoinPartition).partition_frame

    def get_part_frame(self):
        return self.get_opt(OptGoal.JoinPartition).partition_frame

    @property
    def probe_frame(self):
        return self.get_opt(OptGoal.JoinProbe).probe_frame

    def get_probe_frame(self):
        return self.get_opt(OptGoal.JoinProbe).probe_frame

    @property
    def joint_frame(self):
        return self.get_opt(OptGoal.Joint).joint_frame

    def get_joint_frame(self):
        return self.get_opt(OptGoal.Joint).joint_frame

    @property
    def partition_side(self):
        for op_expr in self.operations:
            if op_expr.op_type == MergeExpr:
                if self.name != op_expr.op.left.name and self.name != op_expr.op.right.name:
                    return op_expr.op.left

    def get_partition_side(self):
        for op_expr in self.operations:
            if op_expr.op_type == MergeExpr:
                if self.name != op_expr.op.left.name and self.name != op_expr.op.right.name:
                    return op_expr.op.left

    @property
    def probe_side(self):
        for op_expr in self.operations:
            if op_expr.op_type == MergeExpr:
                if self.name != op_expr.op.left.name and self.name != op_expr.op.right.name:
                    return op_expr.op.right

    def get_probe_side(self):
        for op_expr in self.operations:
            if op_expr.op_type == MergeExpr:
                if self.name != op_expr.op.left.name and self.name != op_expr.op.right.name:
                    return op_expr.op.right

    def find_agg(self):
        for op_expr in self.operations:
            if op_expr.op_type == AggrExpr:
                return op_expr
        return None

    def find_groupby_agg(self):
        for op_expr in self.operations:
            if op_expr.op_type == GroupbyAggrExpr:
                return op_expr
        return None

    def find_this_merge(self):
        if self.is_joint:
            for op_expr in self.operations:
                if op_expr.op_type == MergeExpr:
                    if self.name == op_expr.op.joint.name:
                        return op_expr
        return None

    def find_next_merge(self):
        for op_expr in self.operations:
            if op_expr.op_type == MergeExpr:
                if self.name == op_expr.op.left.name or self.name == op_expr.op.right.name:
                    return op_expr
        return None

    def find_cond(self):
        tmp_list = []
        for op_expr in self.operations:
            if op_expr.op_type == BinCondExpr:
                tmp_list.append(op_expr)
        if tmp_list:
            return tmp_list
        return None

    def find_col_ins(self):
        tmp_list = []
        for op_expr in self.operations:
            if op_expr.op_type == NewColInsert:
                tmp_list.append(op_expr)
        if tmp_list:
            return tmp_list
        return None

    def find_col_proj(self):
        tmp_list = []
        for op_expr in self.operations:
            if op_expr.op_type == ColProj:
                tmp_list.append(op_expr)
        if tmp_list:
            return tmp_list
        return None

    def find_cols_as_probe_key(self):
        cols_list = []
        for op_expr in self.operations:
            if op_expr.op_type == MergeExpr:
                cols_list.append(op_expr.op.right_on)
                if self.name != op_expr.op.joint.name:
                    cols_list += op_expr.op.joint.find_cols_as_probe_key()
        return list(set(cols_list))

    def find_cols_as_part_key(self):
        cols_list = []
        for op_expr in self.operations:
            if op_expr.op_type == MergeExpr:
                cols_list.append(op_expr.op.left_on)
                if self.name != op_expr.op.joint.name:
                    cols_list += op_expr.op.joint.find_cols_as_part_key()
        return list(set(cols_list))

    def find_cols_as_key_tuple(self):
        cols_list = []
        for op_expr in self.operations:
            if op_expr.op_type == MergeExpr:
                if isinstance(op_expr.op.left_on, str) and isinstance(op_expr.op.right_on, str):
                    cols_list.append((op_expr.op.left_on, op_expr.op.right_on))
                elif isinstance(op_expr.op.left_on, list) and isinstance(op_expr.op.right_on, list):
                    if len(op_expr.op.left_on) != len(op_expr.op.right_on):
                        raise ValueError('MergeError: left_on and right_on must be at the same length!')
                    for i in range(len(op_expr.op.left_on)):
                        l_on = op_expr.op.left_on[i]
                        r_on = op_expr.op.right_on[i]
                        cols_list.append((l_on, r_on))
                else:
                    raise NotImplementedError
                if self.name != op_expr.op.joint.name:
                    cols_list += op_expr.op.joint.find_cols_as_key_tuple()
        return list(set(cols_list))

    def get_name_ops(self):
        output = self.name
        for op_expr in self.operations:
            output += op_expr.get_op_name_suffix()
        return output

    def init_context_variable(self):
        self.context_variable[self.name] = self.var_expr
        self.context_variable[self.iter_el.name] = self.iter_el.el

    def add_context_variable(self, vname, vobj):
        self.context_variable[vname] = vobj

    def init_context_constant(self):
        pass

    def define_variables(self):
        result = ''
        for vname in self.context_variable.keys():
            if vname == 'lineitem':
                result += f"{vname} = VarExpr('db->li_dataset')\n"
            elif vname == 'customer':
                result += f"{vname} = VarExpr('db->cu_dataset')\n"
            elif vname == 'orders':
                result += f"{vname} = VarExpr('db->ord_dataset')\n"
            elif vname == 'nation':
                result += f"{vname} = VarExpr('db->na_dataset')\n"
            elif vname == 'region':
                result += f"{vname} = VarExpr('db->re_dataset')\n"
            elif vname == 'part':
                result += f"{vname} = VarExpr('db->pa_dataset')\n"
            elif vname == 'supplier':
                result += f"{vname} = VarExpr('db->su_dataset')\n"
            elif vname == 'partsupp':
                result += f"{vname} = VarExpr('db->ps_dataset')\n"
            else:
                result += f"{vname} = VarExpr('{vname}')\n"
        return result

    def define_constants(self):
        result_seq = VarBindSeq()
        for const in self.context_constant.keys():
            result_seq.push(VarBindExpr(var_expr=self.get_const_var(const),
                                        var_value=ConstantExpr(const)))
        return result_seq

    def get_aggr(self, next_op=None, as_part=False) -> LetExpr:
        return AggrFrame(self).get_aggr_expr(next_op, as_part)

    def get_groupby_aggr(self, next_op=None) -> LetExpr:
        return GroupbyAggrFrame(self).get_groupby_aggr_expr(next_op)

    def reset_index(self, level=0):
        next_df = self.create_copy(location='reset_index')

        next_df.push(OpExpr(op_obj=ColProjExtra(self.retriever.find_possible_index_columns()),
                            op_on=next_df,
                            op_iter=False))

        return next_df

    def unopt_to_sdqlir(self, indent='    ', verbose=True):
        optimizer = Optimizer(opt_on=self,
                              opt_goal=OptGoal.UnOptimized)

        query_obj = optimizer.get_unopt_sdqlir()

        query_str = GenerateSDQLPYCode(query_obj, {})

        query_list = query_str.split('\n')

        query_list = query_list[:query_list.index('True')]

        if verbose:
            print('>> Unoptimized Query <<')

            print(f'{"=" * 60}')

            print('\n'.join(query_list))

            print(f'{"=" * 60}')

        query_list = [f'{indent}{i}' for i in query_list]

        return '\n'.join(query_list)

    def apply(self, func, axis):
        """

        :param func:
        :param axis: 0=columns, 1 = rows
        :return:
        """
        code = str(inspect.getsource(func)).strip()
        # tree = ast.parse(code)
        # nodes = ast.walk(tree)
        # print(ast.dump(tree, indent=4))

        lamb_arg = re.search(r'lambda.*:', code).group()
        lamb_arg = lamb_arg.replace('lambda', '').replace(':', '').strip()

        lamb_op = re.search(r':.*if', code).group()
        lamb_op = lamb_op.replace(':', '').replace('if', '').strip()

        lamb_cond = re.search(r'if.*else', code).group()
        lamb_cond = lamb_cond.replace('if', '').replace('else', '').strip()

        lamb_else = re.search(r'else.*,', code).group()
        lamb_else = lamb_else.replace('else', '').replace(',', '').strip()

        if lamb_else == 'None':
            lamb_else = None
        elif lamb_else == '0':
            lamb_else = 0
        elif lamb_else == '0.0':
            lamb_else = 0.0
        else:
            if lamb_else.isdigit():
                lamb_else = int(lamb_else)
            if lamb_else.isdecimal():
                lamb_else = float(lamb_else)
            else:
                raise NotImplementedError(f'Unsupported Type {lamb_else}.')

        op = eval(lamb_op.replace(f'{lamb_arg}[', 'self['))
        if isinstance(op, (bool, int, float, str)):
            op = ConstantExpr(op)

        cond = eval(lamb_cond.replace(f'{lamb_arg}[', 'self['))

        unopt_cond = cond if isinstance(cond, Expr) else cond.sdql_ir

        if self.is_joint:
            if isinstance(cond, ColOpExternal):
                col_name = cond.col.field
                if col_name in self.partition_side.columns:
                    cond.is_apply_cond = True
                    self.partition_side.push(OpExpr(op_obj=cond,
                                                    op_on=self,
                                                    op_iter=False))

                    apply_cond = CompareExpr(CompareSymbol.NE,
                                                       DicLookupExpr(self.joint_frame.part_frame.part_on_var,
                                                                     self.joint_frame.probe_frame.probe_key_sdql_ir),
                                                       ConstantExpr(None))

                    apply_op = op.sdql_ir

                    return ColOpApply(
                        apply_op=apply_op,
                        apply_cond=apply_cond,
                        apply_else=ConstantExpr(lamb_else),
                        unopt_cond=unopt_cond,
                    )

                    # return IfExpr(condExpr=apply_cond,
                    #               thenBodyExpr=apply_op,
                    #               elseBodyExpr=ConstantExpr(lamb_else))
                elif col_name in self.probe_side.columns:
                    if_expr = IfExpr(condExpr=CompareExpr(CompareSymbol.NE,
                                                          DicLookupExpr(self.joint_frame.part_frame.part_on_var,
                                                                        self.joint_frame.probe_frame.probe_key_sdql_ir),
                                                          ConstantExpr(None)),
                                     thenBodyExpr=op.sdql_ir,
                                     elseBodyExpr=ConstantExpr(lamb_else))

                    cond.is_apply_cond = True

                    return ColOpApply(
                        apply_op=if_expr,
                        apply_cond=cond.sdql_ir,
                        apply_else=ConstantExpr(lamb_else),
                        unopt_cond=unopt_cond,
                    )

                    # if_expr = IfExpr(condExpr=cond.sdql_ir,
                    #                  thenBodyExpr=if_expr,
                    #                  elseBodyExpr=ConstantExpr(lamb_else))

                    # return if_expr
                elif col_name in self.columns:

                    raise NotImplementedError
                else:
                    raise IndexError(f'Cannot find column {col_name}')
            elif isinstance(cond, BinCondExpr):
                cond.is_apply_cond = True

                cond_on = self.retriever.find_cond_on(cond,
                                                      {self.partition_side.name: self.partition_side.cols_out,
                                                       self.probe_side.name: self.probe_side.columns,
                                                       self.name: self.cols_out})

                if len(cond_on) > 1 and self.name in cond_on:
                    cond_on.remove(self.name)

                if len(cond_on) == 1:
                    only_for = cond_on[0]

                    if only_for == self.partition_side.name:
                        apply_cond = cond.replace(rec=DicLookupExpr(self.joint_frame.part_frame.part_on_var,
                                                                    self.joint_frame.probe_frame.probe_key_sdql_ir),
                                                  inplace=False)

                        if isinstance(op, Replaceable):
                            apply_op = op.sdql_ir
                        else:
                            apply_op = op

                        return ColOpApply(
                            apply_op=apply_op,
                            apply_cond=apply_cond,
                            apply_else=ConstantExpr(lamb_else),
                            unopt_cond=unopt_cond,
                        )

                        # return IfExpr(condExpr=apply_cond,
                        #               thenBodyExpr=apply_op,
                        #               elseBodyExpr=ConstantExpr(lamb_else))
                    elif only_for == self.probe_side.name:
                        apply_cond = cond.replace(rec=self.probe_side.iter_el.key,
                                                  inplace=False)

                        if isinstance(op, Replaceable):
                            apply_op = op.sdql_ir
                        else:
                            apply_op = op

                        return ColOpApply(
                            apply_op=apply_op,
                            apply_cond=apply_cond,
                            apply_else=ConstantExpr(lamb_else),
                            unopt_cond=unopt_cond,
                        )

                        # raise NotImplementedError
                        #
                        # return IfExpr(condExpr=apply_cond,
                        #               thenBodyExpr=apply_op,
                        #               elseBodyExpr=ConstantExpr(lamb_else))
                    elif only_for == self.name:
                        cols_in_cond = self.retriever.findall_cols_in_cond(cond)
                        if len(cols_in_cond) == 0:
                            raise NotImplementedError
                        elif len(cols_in_cond) == 1:
                            cols_inserted = self.retriever.findall_col_insert()
                            if cols_inserted:
                                new_col_name = cols_inserted[cols_in_cond[0]]

                                if isinstance(new_col_name, ColEl):
                                    old_col_name = cols_inserted[cols_in_cond[0]].field

                                    if old_col_name in self.partition_side.columns:
                                        apply_cond = cond.replace(
                                            rec=self.retriever.find_lookup_path(self, old_col_name),
                                            inplace=True)
                                    else:
                                        raise NotImplementedError
                                else:
                                    raise NotImplementedError
                            else:
                                raise NotImplementedError

                            if isinstance(op, ColEl):
                                cols_inserted = self.retriever.findall_col_insert()
                                if cols_inserted:
                                    new_col_op = cols_inserted[op.field]

                                    if isinstance(new_col_op, Replaceable):
                                        apply_op = op.replace(
                                            rec=new_col_op.sdql_ir,
                                            inplace=True)
                                    else:
                                        raise NotImplementedError
                                else:
                                    raise NotImplementedError
                            else:
                                raise NotImplementedError

                            return ColOpApply(
                                apply_op=apply_op,
                                apply_cond=apply_cond,
                                apply_else=ConstantExpr(lamb_else),
                                unopt_cond=unopt_cond,
                                original_column=op,
                            )

                            # return IfExpr(condExpr=apply_cond,
                            #               thenBodyExpr=apply_op,
                            #               elseBodyExpr=ConstantExpr(lamb_else))
                    else:
                        raise NotImplementedError
                else:
                    cond_mapper = {tuple(self.partition_side.cols_out):
                                       self.joint_frame.part_lookup(),
                                   tuple(self.probe_side.cols_out): self.probe_side.iter_el.key}

                    apply_cond = cond.replace(rec=None, inplace=False, mapper=cond_mapper)

                    if isinstance(op, Replaceable):
                        apply_op = op.sdql_ir
                    else:
                        apply_op = op

                    return ColOpApply(
                        apply_op=apply_op,
                        apply_cond=apply_cond,
                        apply_else=ConstantExpr(lamb_else),
                        more_cond=[self.joint_frame.part_nonull()],
                        unopt_cond=unopt_cond,
                    )

                    # return IfExpr(condExpr=self.joint_frame.part_nonull(),
                    #               thenBodyExpr=IfExpr(condExpr=apply_cond,
                    #                                   thenBodyExpr=apply_op,
                    #                                   elseBodyExpr=ConstantExpr(lamb_else)),
                    #               elseBodyExpr=ConstantExpr(lamb_else))
            else:
                raise NotImplementedError
        else:
            raise NotImplementedError

    def optimize_obj(self):
        opt = self.get_opt()
        for op_expr in self.operations:
            opt.input(op_expr)

        return opt.output

    def get_history(self):
        return self.operations

    def get_retriever(self) -> Retriever:
        return self.__retriever

    @property
    def retriever(self) -> Retriever:
        return self.__retriever

    def drop_duplicates(self, *args):
        return self

    def squeeze(self):
        return self

    def to_sdqlir(self, optimize=True, indent='    ', verbose=True):
        if optimize:
            return self.opt_to_sdqlir(indent=indent, verbose=verbose)
        else:
            return self.unopt_to_sdqlir(indent=indent, verbose=verbose)

    def dtypes_as_str(self):
        if self.loader:
            return self.loader.to_dtype_str()
        else:
            return ''

    def run_in_sdql(self, datasets=None, optimize=True, indent='    '):
        if datasets is None:
            datasets = []

        pysdql_path = pathlib.Path(os.path.abspath(os.path.dirname(__file__))).parent.parent.absolute()

        tmp_file_path = f'{pysdql_path}/cache/query.py'

        names = ','.join([i.name for i in datasets if isinstance(i, DataFrame)])

        compile_params = ""

        for i in datasets:
            if isinstance(i, DataFrame):
                compile_params += f'"{i}": {i.dtypes_as_str()}'

        # print(compile_params)

        query_list = ['from pysdql.extlib.sdqlpy.sdql_lib import *',
                      f'@sdql_compile({{{compile_params}}})',
                      f'def query({names}):',
                      self.to_sdqlir(indent=indent),
                      f'{indent}return results',
                      '']

        with open(tmp_file_path, 'w') as f:
            f.write('\n'.join(query_list))

        from pysdql.cache.query import query

        datas = [i.loader.to_sdql() for i in datasets if isinstance(i, DataFrame)]

        return query(*datas)

    def get_ret_as(self):
        op_body = self.retriever.find_last_iter()
        if isinstance(op_body, AggrExpr) and op_body.aggr_on.name == self.name:
            return PandasRetType.SERIES
        else:
            return PandasRetType.SCALAR

    def ret_for_agg(self):
        op_body = self.retriever.find_last_iter()
        if isinstance(op_body, AggrExpr) and op_body.aggr_on.name == self.name:
            return True

        return False

    def sort_values(self, by=None, ascending=None):
        return self

    def head(self, val):
        if val == 1:
            last_op = self.retriever.find_last_op()
            col_ins_as_list = self.retriever.findall_col_insert_as_list()

            if isinstance(last_op, ColProj):
                if len(last_op.proj_cols) == 1:
                    target_col = last_op.proj_cols[0]

                    if target_col in col_ins_as_list.keys():
                        target_list = col_ins_as_list[target_col]

                        if len(target_list) == 1:
                            target_expr = target_list[0]

                            if isinstance(target_expr, AggrExpr):
                                target_expr.aggr_on.push(
                                    OpExpr(op_obj=AggrOpRename(aggr_expr=target_expr,
                                                               rename_to=target_col,
                                                               rename_from=f'{list(target_expr.origin_dict.items())[0][0]}'),
                                           op_on=target_expr.aggr_on,
                                           op_iter=True))

                            elif isinstance(target_expr, AggrBinOp):
                                target_expr.on.push(
                                    OpExpr(op_obj=AggrOpRename(aggr_expr=target_expr,
                                                               rename_to=target_col,
                                                               rename_from=target_expr.descriptor),
                                           op_on=target_expr.on,
                                           op_iter=True))
                            else:
                                print(f'Warning: No rename for {target_expr}')

                            return target_expr

        return self

    def get_as_build_end(self):
        return BuildEnd(self)

    def rename_axis(self, mapper):
        next_df = self.create_copy(location='rename_axis')

        next_df.push(OpExpr(op_obj=ColProjExtra(mapper),
                            op_on=next_df,
                            op_iter=False))

        return next_df

    def get_context_unopt(self,
                          rename_last='',
                          conflict_rename_indicator=False,
                          process_until=None,
                          def_const=False,
                          drop_them=None,
                          ):
        return Optimizer(self).get_unopt_context(rename_last=rename_last,
                                                 attr_rename_indicator=conflict_rename_indicator,
                                                 process_until=process_until,
                                                 def_const=def_const,
                                                 drop_them=drop_them,
                                                 )

    def create_copy(self, next_name="", location=None):
        """
        data=None,
        index=None,
        columns=None,
        dtype=None,
        name=None,
        operations=None,
        is_joint=False,
        is_original=True,
        context_variable=None,
        context_constant=None,
        loader=None,
        previous_name=None
        dataset_name=None
        :return:
        """

        verbose = False

        if not location:
            location = "unknown"

        if self.is_original:
            if next_name:
                if verbose:
                    print(f'create and rename copy {next_name} for {self.current_name} at {location}')

                next_df = DataFrame(data=copy.copy(self.data),
                                    index=copy.copy(self.index),
                                    columns=copy.copy(self.columns),
                                    dtype=copy.copy(self.dtypes),
                                    name=next_name,
                                    operations=copy.copy(self.operations),
                                    is_joint=copy.copy(self.is_joint),
                                    is_original=False,
                                    context_variable=copy.copy(self.context_variable),
                                    context_constant=copy.copy(self.context_constant),
                                    loader=copy.deepcopy(self.loader),
                                    previous_name=copy.copy(self.current_name),
                                    dataset_name=copy.copy(self.dataset_name))

                self.copy_cache = next_df
            else:
                if verbose:
                    print(f'create copy for {self.current_name} at {location}')

                next_df = DataFrame(data=copy.copy(self.data),
                                    index=copy.copy(self.index),
                                    columns=copy.copy(self.columns),
                                    dtype=copy.copy(self.dtypes),
                                    name=copy.copy(self.current_name),
                                    operations=copy.copy(self.operations),
                                    is_joint=copy.copy(self.is_joint),
                                    is_original=False,
                                    context_variable=copy.copy(self.context_variable),
                                    context_constant=copy.copy(self.context_constant),
                                    loader=copy.deepcopy(self.loader),
                                    previous_name=copy.copy(self.previous_name),
                                    dataset_name=copy.copy(self.dataset_name))

                self.copy_cache = next_df
        else:
            if verbose:
                print(f'keep {self.current_name} at {location}')

            next_df = self

        return next_df

    @property
    def _merge(self):
        return MergeIndicator()
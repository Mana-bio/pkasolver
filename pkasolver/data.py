# Imports
from typing import Tuple

from rdkit import Chem
from rdkit.Chem import PandasTools
from rdkit.Chem.AllChem import Compute2DCoords
from rdkit.Chem.PandasTools import LoadSDF

PandasTools.RenderImagesInAllDataFrames(images=True)
import random

import numpy as np
import pandas as pd
import torch
import tqdm
from pandas.core.common import flatten
from torch_geometric.data import Data

from pkasolver.chem import create_conjugate
from pkasolver.constants import (
    DEVICE,
    EDGE_FEATURES,
    NODE_FEATURES,
    edge_feat_values,
    node_feat_values,
)

# """Text

# Parameters
# ----------
# param1
#     text1

# Returns
# -------
# type
#     text
# """


def load_data(base: str = "data/Baltruschat") -> dict:
    """Helper function that takes path to working directory and
    returns a dictionary containing the paths to the training and testsets.

    """

    datasets = {
        "Training": f"{base}/combined_training_datasets_unique.sdf",
        "Novartis": f"{base}/novartis_cleaned_mono_unique_notraindata.sdf",
        "Literature": f"{base}/AvLiLuMoVe_cleaned_mono_unique_notraindata.sdf",
    }
    return datasets


def train_validation_set_split(
    df: pd.DataFrame, ratio: float, seed=42
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Splits a Dataframes along the rows randomly into two new Dataframes with a defined size ratio.

    Parameters
    ----------
    df
        DataFrame object
    ratio
        ratio of number of rows of the resulting two dataframes

    Returns
    -------
    (pd.DataFrame, pd.DataFrame)
        dataframes resulting from split
    """
    assert ratio > 0.0 and ratio < 1.0

    random.seed(seed)
    length = len(df)
    split = round(length * ratio)
    ids = list(range(length))
    random.shuffle(ids)
    train_ids = ids[:split]
    val_ids = ids[split:]
    train_df = df.iloc[train_ids, :]
    val_df = df.iloc[val_ids, :]
    return train_df, val_df


# data preprocessing functions - helpers
def import_sdf(sdf_filename: str) -> pd.DataFrame:
    """Imports an sdf file and returns a Dataframe with an additional Smiles column."""
    df = LoadSDF(sdf_filename)
    for mol in df.ROMol:
        Compute2DCoords(mol)
    df["smiles"] = [Chem.MolToSmiles(m) for m in df["ROMol"]]
    return df


def conjugates_to_dataframe(df: pd.DataFrame, mol_col: str = "ROMol") -> pd.DataFrame:
    """Takes DataFrame and returns a DataFrame with a column of calculated conjugated molecules.

    Parameters
    ----------
    df
        DataFrame object
    mol_col
        name of the column containing the Chem.rdchem.Mol objects

    Returns
    -------
    pd.DataFrame
        input dataframes with additional "Conjugates" column
    """
    conjugates = []
    for i in tqdm.tqdm(range(len(df.index))):
        mol = df[mol_col][i]
        index = int(df.marvin_atom[i])
        pka = float(df.marvin_pKa[i])
        try:
            conj = create_conjugate(mol, index, pka, ignore_danger=True)
            conjugates.append(conj)
        except Exception as e:
            print(f"Could not create conjugate of mol number {i}")
            print(e)
            conjugates.append(mol)
    df["Conjugates"] = conjugates
    return df


def sort_conjugates(
    df: pd.DataFrame, mol_col_1: str = "ROMol", mol_col_2: str = "Conjugates"
) -> pd.DataFrame:
    """Takes DataFrame and sorts the molecules in the two specified columns into two new columns, "protonated" and "deprotonated".

    Parameters
    ----------
    df
        DataFrame object
    mol_col_1, mol_col_2
        name of the columns containing the Chem.rdchem.Mol objects

    Returns
    -------
    pd.DataFrame
        input dataframes with the two columns specified, replaced by the columns "protonated" and "deprotonated"
    """
    prot = []
    deprot = []
    for i in range(len(df.index)):
        indx = int(
            df.marvin_atom[i]
        )  # mark reaction center where (de)protonation takes place
        mol = df[mol_col_1][i]
        conj = df[mol_col_2][i]

        charge_mol = int(mol.GetAtomWithIdx(indx).GetFormalCharge())
        charge_conj = int(conj.GetAtomWithIdx(indx).GetFormalCharge())

        if charge_mol < charge_conj:
            prot.append(conj)
            deprot.append(mol)

        elif charge_mol > charge_conj:
            prot.append(mol)
            deprot.append(conj)
        else:
            print("prot = deprot")
            prot.append(mol)
            deprot.append(conj)
    df["protonated"] = prot
    df["deprotonated"] = deprot
    df = df.drop(columns=["ROMol", "Conjugates"])
    return df


# data preprocessing functions - main
def preprocess(sdf_filename: str) -> pd.DataFrame:
    """Takes path of sdf file containing pkadata and returns a dataframe with column for protonated and deprotonated molecules."""
    df = import_sdf(sdf_filename)
    df = conjugates_to_dataframe(df)
    df = sort_conjugates(df)
    df["pKa"] = df["pKa"].astype(float)
    return df


def preprocess_all(sdf_files) -> dict:
    """Takes dictionary of pka data sets containing paths to sdf files, preprocesses them to Dataframes
    with protonated and deprotonated molecules and returns them in a dictionary.

    """
    datasets = {}
    for name, sdf_filename in sdf_files.items():
        print(f"{name} : {sdf_filename}")
        print("###############")
        datasets[name] = preprocess(sdf_filename)
    return datasets


# Random Forrest/ML preparation functions
def make_stat_variables(
    df, X_list: list, y_name: list
) -> Tuple[np.ndarray, np.ndarray]:
    """Takes Pandas DataFrame  and and returns one numpy array from all
    column specified in X_list and one from the column specified in y_list.

    """
    X = np.asfarray(df[X_list], float)
    y = np.asfarray(df[y_name], float).reshape(-1)
    return X, y


# Neural net data functions - helpers
class PairData(Data):
    """Extension of the Pytorch Geometric Data Class, which additionally
    takes a conjugated molecules in form of the edge_index2 and x2 input"""

    def __init__(
        self,
        # NOTE: everything for protonated
        edge_index_p,
        edge_attr_p,
        x_p,
        charge_p,
        # everhtying for deprotonated
        edge_index_d,
        edge_attr_d,
        x_d,
        charge_d,
    ):
        super(PairData, self).__init__()
        self.edge_index_p = edge_index_p
        self.edge_index_d = edge_index_d

        self.x_p = x_p
        self.x_d = x_d

        self.edge_attr_p = edge_attr_p
        self.edge_attr_d = edge_attr_d

        self.charge_prot = charge_p
        self.charge_deprot = charge_d
        if x_p is not None:
            self.num_nodes = len(x_p)

    def __inc__(self, key, value, *args, **kwargs):
        if key == "edge_index_p":
            return self.x_p.size(0)
        if key == "edge_index_d":
            return self.x_d.size(0)
        else:
            return super().__inc__(key, value, *args, **kwargs)


def calculate_nr_of_features(feature_list: list) -> int:
    """Calculates number of nodes and edge features from input list"""
    i_n = 0
    if all(elem in node_feat_values for elem in feature_list):
        for feat in feature_list:
            i_n += len(node_feat_values[feat])
    elif all(elem in edge_feat_values for elem in feature_list):
        for feat in feature_list:
            i_n += len(edge_feat_values[feat])
    else:
        raise RuntimeError()
    return i_n


def make_nodes(mol: Chem.rdchem.Mol, atom_idx: int, n_features: dict) -> torch.Tensor:
    """Takes an rdkit.Mol, the atom index of the reaction center and a dictionary of node feature functions
    and returns a torch.tensor of node features for all atoms of one molecule.

    Parameters
    ----------
    mol
        input molecule
    atom_idx
        atom index of ionization center
    n_features
        dictionary containing functions for node feature generation

    Returns
    -------
    torch.Tensor
        tensor with dimensions num_nodes(atoms) x num_node_features.
    """

    x = []
    for atom in mol.GetAtoms():
        node = []
        for feat in n_features.values():
            node.append(feat(atom, atom_idx))
        node = list(flatten(node))
        # node = [int(x) for x in node]
        x.append(node)
    return torch.tensor(np.array([np.array(xi) for xi in x]), dtype=torch.float)


def make_edges_and_attr(mol, e_features) -> Tuple[torch.Tensor, torch.Tensor]:
    """Takes an rdkit.Mol and a dictionary of edge feature functions
    and returns one tensor containing adjacency information and one of edge features for all edges of one molecule.
    Parameters
    ----------
    mol
        input molecule
    e_features
        dictionary containing functions for edge feature generation

    Returns
    -------
    edge_index
        tensor of dimension 2 x num_edges
    edge_attr
        tensor with dimensions num_edge) x num_edge_features.

    """

    edges = []
    edge_attr = []
    for bond in mol.GetBonds():
        edges.append(
            np.array(
                [
                    [bond.GetBeginAtomIdx()],
                    [bond.GetEndAtomIdx()],
                ]
            )
        )
        edges.append(
            np.array(
                [
                    [bond.GetEndAtomIdx()],
                    [bond.GetBeginAtomIdx()],
                ]
            )
        )
        edge = []
        for feat in e_features.values():
            edge.append(feat(bond))
        edge = list(flatten(edge))
        edge_attr.extend([edge] * 2)

    edge_index = torch.tensor(np.hstack(np.array(edges)), dtype=torch.long)
    edge_attr = torch.tensor(np.array(edge_attr), dtype=torch.float)
    return edge_index, edge_attr


# function to select which features to use in the graph data
def make_features_dicts(all_features, feat_list):
    """Take a dict of all features and a list of strings with all desired features
    and return a dict with these features

    """
    return {x: all_features[x] for x in feat_list}


# def mol_to_features_old(
#     row, n_features: dict, e_features: dict, protonation_state: str
# ):
#     if protonation_state == "protonated":
#         node = make_nodes(row.protonated, row.marvin_atom, n_features)
#         edge_index, edge_attr = make_edges_and_attr(row.protonated, e_features)
#         charge = np.sum([a.GetFormalCharge() for a in row.protonated.GetAtoms()])
#         return node, edge_index, edge_attr, charge
#     elif protonation_state == "deprotonated":
#         node = make_nodes(row.deprotonated, row.marvin_atom, n_features)
#         edge_index, edge_attr = make_edges_and_attr(row.deprotonated, e_features)
#         charge = np.sum([a.GetFormalCharge() for a in row.deprotonated.GetAtoms()])
#         return node, edge_index, edge_attr, charge
#     else:
#         raise RuntimeError()


def mol_to_features(
    mol: Chem.rdchem.Mol, atom_idx: int, n_features: dict, e_features: dict
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, int]:
    """Create the node and edge feature tensors from the input molecule
    Parameters
    ----------
    mol
        input molecule
    atom_idx
        atom index of ionization center
    n_features
        dictionary containing functions for node feature generation
    e_features
        dictionary containing functions for node feature generation

    Returns
    -------
    nodes
        tensor with dimensions num_nodes(atoms) x num_node_features.
    edge_index
        tensor of dimension 2 x num_edges
    edge_attr
        tensor with dimensions num_edge) x num_edge_features.
    charge
        molecule charge
    """

    nodes = make_nodes(mol, atom_idx, n_features)
    edge_index, edge_attr = make_edges_and_attr(mol, e_features)
    charge = np.sum([a.GetFormalCharge() for a in mol.GetAtoms()])
    return nodes, edge_index, edge_attr, charge


def mol_to_paired_mol_data(
    prot: Chem.rdchem.Mol,
    deprot: Chem.rdchem.Mol,
    atom_idx: int,
    n_features: dict,
    e_features: dict,
) -> PairData:
    """Take a DataFrame row, a dict of node feature functions and a dict of edge feature functions
    and return a Pytorch PairData object.
    """
    node_p, edge_index_p, edge_attr_p, charge_p = mol_to_features(
        prot, atom_idx, n_features, e_features
    )
    node_d, edge_index_d, edge_attr_d, charge_d = mol_to_features(
        deprot, atom_idx, n_features, e_features
    )

    data = PairData(
        edge_index_p=edge_index_p,
        edge_attr_p=edge_attr_p,
        x_p=node_p,
        charge_p=charge_p,
        edge_index_d=edge_index_d,
        edge_attr_d=edge_attr_d,
        x_d=node_d,
        charge_d=charge_d,
    )
    return data


def mol_to_single_mol_data(
    mol: Chem.rdchem.Mol,
    atom_idx: int,
    n_features: dict,
    e_features: dict,
) -> Data:
    """Take a DataFrame row, a dict of node feature functions and a dict of edge feature functions
    and return a Pytorch Data object.
    """
    node_p, edge_index_p, edge_attr_p, charge = mol_to_features(
        mol, atom_idx, n_features, e_features
    )
    return Data(x=node_p, edge_index=edge_index_p, edge_attr=edge_attr_p), charge


def make_pyg_dataset_from_dataframe(
    df: pd.DataFrame, list_n: list, list_e: list, paired=False, mode: str = "all"
) -> list:
    """Take a Dataframe, a list of strings of node features, a list of strings of edge features
    and return a List of PyG Data objects.
    """
    print(f"Generating data with paired boolean set to: {paired}")

    if paired is False and mode not in ["protonated", "deprotonated"]:
        raise RuntimeError(f"Wrong combination of {mode} and {paired}")

    selected_node_features = make_features_dicts(NODE_FEATURES, list_n)
    selected_edge_features = make_features_dicts(EDGE_FEATURES, list_e)
    if paired:
        dataset = []
        for i in df.index:
            m = mol_to_paired_mol_data(
                df.protonated[i],
                df.deprotonated[i],
                df.marvin_atom[i],
                selected_node_features,
                selected_edge_features,
            )
            m.reference_value = torch.tensor([df.pKa[i]], dtype=torch.float32)
            m.ID = df.ID[i]
            m.to(device=DEVICE)  # NOTE: put everything on the GPU
            dataset.append(m)
        return dataset
    else:
        print(f"Generating data with {mode} form")
        dataset = []
        for i in df.index:
            if mode == "protonated":
                m, molecular_charge = mol_to_single_mol_data(
                    df.protonated[i],
                    df.marvin_atom[i],
                    selected_node_features,
                    selected_edge_features,
                )
            elif mode == "deprotonated":
                m, molecular_charge = mol_to_single_mol_data(
                    df.deprotonated[i],
                    df.marvin_atom[i],
                    selected_node_features,
                    selected_edge_features,
                )
            else:
                raise RuntimeError()
            m.reference_value = torch.tensor([df.pKa[i]], dtype=torch.float32)
            m.ID = df.ID[i]
            m.to(device=DEVICE)  # NOTE: put everything on the GPU
            dataset.append(m)
        return dataset


def make_paired_pyg_data_from_mol(
    mol: Chem.rdchem.Mol, selected_node_features: dict, selected_edge_features: dict
) -> PairData:
    """Take a rdkit mol and generate a PyG Data object."""

    props = mol.GetPropsAsDict()
    try:
        pka = props["pKa"]
    except KeyError as e:
        print(f"No pKa found for molecule: {props}")
        print(props)
        print(Chem.MolToSmiles(mol))
        raise e
    if "epik_atom" in props.keys():
        atom_idx = props["epik_atom"]
    elif "marvin_atom" in props.keys():
        atom_idx = props["marvin_atom"]
    else:
        print(f"No reaction center foundfor molecule: {props}")
        print(props)
        print(Chem.MolToSmiles(mol))
        raise RuntimeError()

    try:
        conj = create_conjugate(mol, atom_idx, pka)
    except AssertionError as e:
        print(f"mol is failing because: {e}")
        raise e

    # sort mol and conj into protonated and deprotonated molecule
    if int(mol.GetAtomWithIdx(atom_idx).GetFormalCharge()) > int(
        conj.GetAtomWithIdx(atom_idx).GetFormalCharge()
    ):
        prot = mol
        deprot = conj
    else:
        prot = conj
        deprot = mol

    # create PairData object from prot and deprot with the selected node and edge features
    m = mol_to_paired_mol_data(
        prot,
        deprot,
        atom_idx,
        selected_node_features,
        selected_edge_features,
    )
    m.x = torch.tensor(pka, dtype=torch.float32)

    if "pka_number" in props.keys():
        m.pka_type = props["pka_number"]
    elif "marvin_pKa_type" in props.keys():
        m.pka_type = props["marvin_pKa_type"]
    else:
        m.pka_type = ""

    try:
        m.ID = props["ID"]
    except:
        m.ID = ""
    return m


def slice_list(input_list: list, size: int) -> list:
    "take a list and devide its items"
    input_size = len(input_list)
    slice_size = input_size // size
    remain = input_size % size
    result = []
    iterator = iter(input_list)
    for i in range(size):
        result.append([])
        for j in range(slice_size):
            result[i].append(next(iterator))
        if remain:
            result[i].append(next(iterator))
            remain -= 1
    return result


def cross_val_lists(sliced_lists: list, num: int) -> Tuple[list, list]:
    not_flattend = [x for i, x in enumerate(sliced_lists) if i != num]
    train_list = [item for subl in not_flattend for item in subl]
    val_list = sliced_lists[num]
    return train_list, val_list

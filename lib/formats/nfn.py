import json
import re
from collections import defaultdict
from collections import namedtuple
from dataclasses import dataclass
from dataclasses import field
from typing import Any

import pandas as pd
from dateutil.parser import parse

from .. import util

STARTED_AT = "classification_started_at"

WorkflowString = namedtuple("WorkflowString", "value label")


@dataclass
class WorkflowStrings:
    label_strings: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    value_strings: dict[str, WorkflowString] = field(default_factory=dict)


# #####################################################################################
def read(args):
    """Read and convert the input CSV data."""
    df = pd.read_csv(args.input_file, dtype=str)

    # Get defaults from the dataframe
    args.workflow_id = get_workflow_id(df, args)
    args.workflow_name = get_workflow_name(df)
    args.title = f'Summary of "{args.workflow_name}" ({args.workflow_id})'

    # Get the classifications for the workflow
    df = df.loc[df.workflow_id == str(args.workflow_id), :]

    # A hack to workaround crap coming back from Zooniverse
    workflow_strings = get_workflow_strings(args.workflow_csv, args.workflow_id)

    # Extract the various json blobs
    df, column_types = extract_annotations(df, workflow_strings)
    df, column_types = extract_subject_data(df, column_types)
    df, column_types = extract_metadata(df, column_types)

    # Get the subject_id from the subject_ids list, use the first one
    df[args.group_by] = df.subject_ids.map(lambda x: int(str(x).split(";")[0]))

    # Remove unwanted columns
    unwanted = " user_id user_ip subject_ids subject_data subject_retired ".split()
    unwanted_columns = [c for c in df.columns if c.lower() in unwanted]
    df = df.drop(unwanted_columns, axis=1)
    column_types = {k: v for k, v in column_types.items() if k.lower() not in unwanted}


# #############################################################################
def extract_annotations(df, workflow_strings):
    """Extract annotations from the json object in the annotations column.

    Annotations are nested json blobs with a WTF format. Part of the WTF is that each
    record can have a different format which means that we need to update the
    column_types in every record eventho they are mostly quite similar.
    """
    column_types: dict[str, str] = {}

    annotations = df.annotations.map(json.loads)
    data = [flatten_annotations(column_types, a, workflow_strings) for a in annotations]
    data = pd.DataFrame(data, index=df.index)

    df = pd.concat([df, data], axis=1)

    column_types, renames = rename_columns(column_types)

    return df.rename(columns=renames).drop(["annotations"], axis=1), column_types


def flatten_annotations(column_types, annotations, workflow_strings):
    """Flatten annotations.

    Annotations are nested json blobs with a peculiar data format. So we flatten it to
    make it easier to reconcile. We also need to consider that different tasks may have
    the same label, in that case we add a tie breaker.
    """
    annos: dict[str, Any] = {}  # Key is a unique column name & the value is anything

    for anno in annotations:
        flatten_annotation(column_types, annos, anno, "", workflow_strings)

    return annos


def flatten_annotation(column_types, annos, anno, anno_id, workflow_strings):
    """Flatten one annotation recursively."""
    anno_id = anno.get("task", anno_id)

    match anno:
        case {"value": [str(), *__], **___}:
            list_annotation(column_types, annos, anno)
        case {"value": list(), **__}:
            subtask_annotation(column_types, annos, anno, anno_id, workflow_strings)
        case {"select_label": _, **__}:
            select_label_annotation(column_types, annos, anno)
        case {"task_label": _, **__}:
            task_label_annotation(column_types, annos, anno)
        case {"tool_label": _, "width": __, **___}:
            box_annotation(column_types, annos, anno)
        case {"tool_label": _, "x1": __, **___}:
            line_annotation(column_types, annos, anno)
        case {"tool_label": _, "x": __, **___}:
            point_annotation(column_types, annos, anno)
        case {"tool_label": _, "details": __, **___}:
            workflow_annotation(column_types, annos, anno, anno_id, workflow_strings)
        case _:
            print(f"Annotation type not found: {anno}")


def list_annotation(column_types, annos, anno):
    """Handle a list of literals annotation."""
    key = unique_name(column_types, anno["task_label"])
    values = sorted(anno.get("value", ""))
    annos[key] = " ".join(values)
    column_types[key] = "text"


def subtask_annotation(column_types, annos, anno, anno_id, workflow_strings):
    """Handle an annotation with subtasks."""
    anno_id = anno.get("task", anno_id)
    for subtask in anno["value"]:
        flatten_annotation(column_types, annos, subtask, anno_id, workflow_strings)


def select_label_annotation(column_types, annos, anno):
    """Handle a select label annotation."""
    key = unique_name(column_types, anno["select_label"])
    option = anno.get("option")
    value = anno.get("label", "") if option else anno.get("value", "")
    annos[key] = value
    column_types[key] = "select"


def task_label_annotation(column_types, annos, anno):
    """Handle a label annotation."""
    key = unique_name(column_types, anno["task_label"])
    annos[key] = anno.get("value", "")
    column_types[key] = "text"


def box_annotation(column_types, annos, anno):
    label = "{}: box".format(anno["tool_label"])
    label = unique_name(column_types, label)
    column_types[label] = "box"
    annos[label] = json.dumps(
        {
            "left": round(anno["x"]),
            "right": round(anno["x"] + anno["width"]),
            "top": round(anno["y"]),
            "bottom": round(anno["y"] + anno["height"]),
        }
    )


def line_annotation(column_types, annos, anno):
    label = "{}: line".format(anno["tool_label"])
    label = unique_name(column_types, label)
    column_types[label] = "line"
    annos[label] = json.dumps(
        {
            "x1": round(anno["x1"]),
            "y1": round(anno["y1"]),
            "x2": round(anno["x2"]),
            "y2": round(anno["y2"]),
        }
    )


def point_annotation(column_types, annos, anno):
    label = "{}: point".format(anno["tool_label"])
    label = unique_name(column_types, label)
    annos[label] = json.dumps({"x": round(anno["x"]), "y": round(anno["y"])})
    column_types[label] = "point"


def workflow_annotation(column_types, annos, anno, anno_id, workflow_strings):
    """Get the value of an annotation from workflow data.

    We are trying to match a coded value (UUID-like) with strings in the workflow
    description. We can have a text value or a (multi-)select value.
    """
    label = "unknown"

    # Get all possible strings for the annotation
    candidates = []
    for key, value in workflow_strings.label_strings.items():
        if key.startswith(anno_id) and key.endswith("details"):
            candidates.append(value)
    labels = candidates[-1] if candidates else []

    # Loop thru the UUID values
    for i, detail in enumerate(anno["details"]):
        outer_value = detail["value"]

        # If it's a list then we have a (multi-)select value
        if isinstance(outer_value, list):
            type_ = "select"
            values = []

            for item in outer_value:
                # We found the workflow string for the UUID
                if item["value"] in workflow_strings.value_strings:
                    value, label = workflow_strings.value_strings[item["value"]]
                    values.append(value)

                # Paranoia: Cannot find string in workflow
                else:
                    value = item["value"]
                    label = item.get("label", "unknown")
                    values.append(value)

            value = ",".join(v for v in values if v)

        # It's a single text value
        else:
            value = outer_value
            label = labels[i] if i < len(labels) else "unknown"
            type_ = "text"

        # Now we can use the reconstructed annotation
        label = f"{anno['tool_label']}.{label}"
        label = unique_name(column_types, label)
        annos[label] = value
        column_types[label] = type_


def unique_name(columns_types, name):
    """Make the column name unique."""
    name = name.strip()
    base = name
    i = 1
    while name in columns_types:
        i += 1
        name = f"{base} #{i}"
    return name


# #############################################################################
def extract_subject_data(df, column_types):
    """Extract subject data from the json object in the subject_data column.

    We prefix the new column names with "subject_" to keep them separate from
    the other data df columns. The subject data json looks like:
        {<subject_id>: {"key_1": "value_1", "key_2": "value_2", ...}}
    """
    data = df.subject_data.map(json.loads).apply(lambda x: list(x.values())[0]).tolist()
    data = pd.DataFrame(data, index=df.index)
    df = df.drop(["subject_data"], axis=1)

    if "retired" in data.columns:
        data = data.drop(["retired"], axis=1)

    if "id" in data.columns:
        data = data.rename(columns={"id": "external_id"})

    columns_ = ["subject_" + re.sub(r"\W+", "_", c).strip("_") for c in data.columns]

    columns_ = dict(zip(data.columns, columns_))
    data = data.rename(columns=columns_)

    df = pd.concat([df, data], axis=1)

    # Put the subject columns into the column_types: They're all 'same'
    for name in data.columns:
        column_types[name] = "same"

    return df, column_types


# #############################################################################
def extract_metadata(df, column_types):
    """Extract a few field from the metadata JSON object."""

    def _extract_date(value):
        return parse(value).strftime("%d-%b-%Y %H:%M:%S")

    data = df.metadata.map(json.loads).tolist()
    data = pd.DataFrame(data, index=df.index)

    df[STARTED_AT] = data.started_at.map(_extract_date)

    name = "classification_finished_at"
    df[name] = data.finished_at.map(_extract_date)

    return df.drop(["metadata"], axis=1), column_types


# #############################################################################
def get_workflow_id(df, args):
    """Pull the workflow ID from the data frame if it was not given."""
    if args.workflow_id:
        return args.workflow_id

    workflow_ids = df.workflow_id.unique()

    if len(workflow_ids) > 1:
        util.error_exit(
            "There are multiple workflows in this file. "
            "You must provide a workflow ID as an argument."
        )

    return workflow_ids[0]


def get_workflow_name(df):
    """Extract and format the workflow name from the data df."""
    workflow_name = ""
    try:
        workflow_name = df.workflow_name.iloc[0]
        workflow_name = re.sub(r"^[^_]*_", "", workflow_name)
    except KeyError:
        util.error_exit("Workflow name not found in classifications file.")
    return workflow_name


# #####################################################################################
def get_workflow_strings(workflow_csv, workflow_id):
    """Get strings from the workflow when they're not in the annotations."""
    value_strings = {}
    if not workflow_csv:
        return value_strings

    df = pd.read_csv(workflow_csv)
    df = df.loc[df.workflow_id == int(workflow_id), :]
    row = df.iloc[-1]

    strings = {k: v for k, v in json.loads(row["strings"]).items()}

    instructions = {}
    label_strings = defaultdict(list)
    for key, value in strings.items():
        if key.endswith("instruction"):
            parts = key.split(".")
            key = ".".join(parts[:-1])
            value = value.strip()
            instructions[key] = value
            key = ".".join(parts[:-2])
            if key:
                label_strings[key].append(value)

    def _task_dive(node):
        if isinstance(node, dict) and node.get("value"):
            string = strings.get(node.get("label"))
            if string:
                string = string.strip()
                label = node["label"].strip()
                labels = [v for k, v in instructions.items() if label.startswith(k)]
                label = labels[-1] if labels else ""
                value_strings[node["value"]] = WorkflowString(string, label)
        elif isinstance(node, dict):
            for child in node.values():
                _task_dive(child)
        elif isinstance(node, list):
            for child in node:
                _task_dive(child)

    annos = json.loads(row["tasks"])
    _task_dive(annos)

    return WorkflowStrings(label_strings=label_strings, value_strings=value_strings)


# #####################################################################################
def rename_columns(columns_types):
    """Rename columns to use a "#1" suffix if there exists a "#2" suffix."""
    renames = {}

    for name in columns_types.keys():
        old_name = name[:-3]
        if name.endswith("#2") and old_name in columns_types:
            renames[old_name] = old_name + " #1"

    new_columns: dict[str, str] = {}
    for name, type_ in columns_types.items():
        new_name = renames.get(name, name)
        new_columns[new_name] = type_

    return new_columns, renames

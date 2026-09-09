import pandas as pd
import numpy as np
from sklearn.model_selection import GroupKFold
from sklearn.linear_model import LinearRegression
from sklearn.metrics import root_mean_squared_error, mean_absolute_error
from sklearn.preprocessing import LabelEncoder

feature_cols = [
    'frame_index', 'img_w', 'img_h',
    'shoulder_x', 'shoulder_y', 'wrist_x', 'wrist_y',
    'elbow_x', 'elbow_y', 'p_throws', 'venue_id',
    'off_shoulder_x', 'off_shoulder_y', 'ball_x', 'ball_y',
    'ball_angle', 'wrist_angle',
]
TARGET_COL = 'true_arm_angle'


def load_labeled_data(path):
    labels = pd.read_csv(path)
    data = labels.loc[labels['status'] == "labeled"].copy()
    data['p_throws'] = LabelEncoder().fit_transform(data['p_throws'])
    return data.dropna(subset=feature_cols + [TARGET_COL])


def train_model(data):
    x, y = data[feature_cols], data[TARGET_COL]
    predictions = np.zeros(len(x))

    model = None
    for train_idx, test_idx in GroupKFold(n_splits=8).split(x, y, groups=data['pitcher_id']):
        model = LinearRegression().fit(x.iloc[train_idx], y.iloc[train_idx])
        predictions[test_idx] = model.predict(x.iloc[test_idx])

    rmse = root_mean_squared_error(y, predictions)
    mae = mean_absolute_error(y, predictions)
    return model, rmse, mae


if __name__ == "__main__":
    data = load_labeled_data("labels.csv")
    model, rmse, mae = train_model(data)
    print(f"RMSE: {rmse:.4f}, MAE: {mae:.4f}")
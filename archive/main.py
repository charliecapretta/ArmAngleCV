# This is a sample Python script.

# Press ⌃R to execute it or replace it with your code.
# Press Double ⇧ to search everywhere for classes, files, tool windows, actions, and settings.

import pandas as pd

raw = pd.read_csv('/Users/charliecapretta/Desktop/ArmAngleAttempt/Pitcher_Motion_Data.csv')
cleanSevant = raw.columns

# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    print(cleanSevant)

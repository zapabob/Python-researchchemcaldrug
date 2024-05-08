import optuna
from sklearn.model_selection import GridSearchCV, KFold, StratifiedKFold, learning_curve
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error, roc_curve, auc, precision_score, recall_score, confusion_matrix
import time
from keras.wrappers.scikit_learn import KerasRegressor
from keras.models import Sequential, load_model
from keras.layers import Dense, Dropout
from sklearn.model_selection import train_test_split
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.preprocessing import StandardScaler
import shap
from chembl_webresource_client.new_client import new_client

class IC50Predictor:
    def __init__(self, data_dat, labels_dat, data_net, labels_net):
        self.data_dat = data_dat
        self.labels_dat = labels_dat
        self.data_net = data_net
        self.labels_net = labels_net
        self.model = None
        self.train_data_dat, self.test_data_dat, self.train_labels_dat, self.test_labels_dat = train_test_split(data_dat, labels_dat, test_size=0.2, random_state=42)
        self.train_data_net, self.test_data_net, self.train_labels_net, self.test_labels_net = train_test_split(data_net, labels_net, test_size=0.2, random_state=42)
        self.scaler = StandardScaler()
        self.train_data_scaled_dat = self.scaler.fit_transform(self.train_data_dat)
        self.test_data_scaled_dat = self.scaler.transform(self.test_data_dat)
        self.train_data_scaled_net = self.scaler.fit_transform(self.train_data_net)
        self.test_data_scaled_net = self.scaler.transform(self.test_data_net)

    def create_model(self, units1=128, units2=64, dropout=0.2):
        model = Sequential([
            Dense(units1, activation='relu', input_shape=(self.train_data_dat.shape[1],)),
            Dropout(dropout),
            Dense(units2, activation='relu'),
            Dense(2, activation='sigmoid')
        ])
        model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
        return model

    def objective(self, trial):
        params = {
            'units1': trial.suggest_int('units1', 32, 1024),
            'units2': trial.suggest_int('units2', 32, 1024),
            'dropout': trial.suggest_float('dropout', 0.1, 0.5)
        }
        model = self.create_model(**params)
        
        kf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
        scores_dat = []
        scores_net = []
        for train_index, val_index in kf.split(self.train_data_scaled_dat, self.train_labels_dat):
            x_train_dat, x_val_dat = self.train_data_scaled_dat[train_index], self.train_data_scaled_dat[val_index]
            y_train_dat, y_val_dat = self.train_labels_dat[train_index], self.train_labels_dat[val_index]
            x_train_net, x_val_net = self.train_data_scaled_net[train_index], self.train_data_scaled_net[val_index]
            y_train_net, y_val_net = self.train_labels_net[train_index], self.train_labels_net[val_index]
            model.fit(np.concatenate((x_train_dat, x_train_net), axis=0),
                      np.column_stack((y_train_dat, y_train_net)),
                      epochs=100, batch_size=32,
                      validation_data=(np.concatenate((x_val_dat, x_val_net), axis=0), np.column_stack((y_val_dat, y_val_net))),
                      verbose=0)
            y_pred_dat = model.predict(x_val_dat)[:, 0]
            y_pred_net = model.predict(x_val_net)[:, 1]
            y_pred_classes_dat = (y_pred_dat > 0.5).astype(int)
            y_pred_classes_net = (y_pred_net > 0.5).astype(int)
            score_dat = precision_score(y_val_dat, y_pred_classes_dat)
            score_net = precision_score(y_val_net, y_pred_classes_net)
            scores_dat.append(score_dat)
            scores_net.append(score_net)
        
        return np.mean(scores_dat + scores_net)

    def optuna_optimize(self, n_trials=100):
        start_time = time.time()
        study = optuna.create_study(direction='maximize')
        study.optimize(self.objective, n_trials=n_trials)
        optuna_time = time.time() - start_time
        print(f"Optuna best parameters: {study.best_params}")
        print(f"Optuna best score: {study.best_value}")
        print(f"Optuna optimization time: {optuna_time:.2f} seconds")
        return study.best_params, study

    def grid_search_optimize(self):
        param_grid = {
            'units1': [32, 64, 128, 256, 512, 1024],
            'units2': [32, 64, 128, 256, 512, 1024],
            'dropout': [0.1, 0.2, 0.3, 0.4, 0.5]
        }
        model = KerasRegressor(build_fn=self.create_model, verbose=0)
        start_time = time.time()
        grid_search = GridSearchCV(estimator=model, param_grid=param_grid, cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42), scoring='precision')
        grid_result = grid_search.fit(np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0),
                                      np.column_stack((self.train_labels_dat, self.train_labels_net)))
        grid_search_time = time.time() - start_time
        print(f"Grid search best parameters: {grid_search.best_params_}")
        print(f"Grid search best score: {grid_search.best_score_}")
        print(f"Grid search optimization time: {grid_search_time:.2f} seconds")
        return grid_search.best_params_

    def train_and_evaluate(self, best_params):
        self.model = self.create_model(**best_params)
        history = self.model.fit(np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0),
                                 np.column_stack((self.train_labels_dat, self.train_labels_net)),
                                 epochs=100, batch_size=32, validation_split=0.1)

        # Plot training & validation loss values
        plt.figure()
        plt.plot(history.history['loss'])
        plt.plot(history.history['val_loss'])
        plt.title('Model loss')
        plt.ylabel('Loss')
        plt.xlabel('Epoch')
        plt.legend(['Train', 'Validation'], loc='upper left')
        plt.show()

        # Evaluate the model on the test data
        y_pred_prob_dat = self.model.predict(self.test_data_scaled_dat)[:, 0]
        y_pred_prob_net = self.model.predict(self.test_data_scaled_net)[:, 1]
        y_pred_classes_dat = (y_pred_prob_dat > 0.5).astype(int)
        y_pred_classes_net = (y_pred_prob_net > 0.5).astype(int)
        
        # ROC AUC
        fpr_dat, tpr_dat, _ = roc_curve(self.test_labels_dat, y_pred_prob_dat)
        roc_auc_dat = auc(fpr_dat, tpr_dat)
        fpr_net, tpr_net, _ = roc_curve(self.test_labels_net, y_pred_prob_net)
        roc_auc_net = auc(fpr_net, tpr_net)

        plt.figure()
        plt.plot(fpr_dat, tpr_dat, color='darkorange', lw=2, label=f'ROC curve for DAT (area = {roc_auc_dat:.2f})')
        plt.plot(fpr_net, tpr_net, color='darkblue', lw=2, label=f'ROC curve for NET (area = {roc_auc_net:.2f})')
        plt.plot([0, 1], [0, 1], color='navy', lw=2, linestyle='--')
        plt.xlim([0.0, 1.0])
        plt.ylim([0.0, 1.05])
        plt.xlabel('False Positive Rate')
        plt.ylabel('True Positive Rate')
        plt.title('Receiver Operating Characteristic')
        plt.legend(loc="lower right")
        plt.show()

        # Precision, Recall, Confusion Matrix
        precision_dat = precision_score(self.test_labels_dat, y_pred_classes_dat)
        recall_dat = recall_score(self.test_labels_dat, y_pred_classes_dat)
        cm_dat = confusion_matrix(self.test_labels_dat, y_pred_classes_dat)

        precision_net = precision_score(self.test_labels_net, y_pred_classes_net)
        recall_net = recall_score(self.test_labels_net, y_pred_classes_net)
        cm_net = confusion_matrix(self.test_labels_net, y_pred_classes_net)

        print("DAT:")
        print(f"Precision: {precision_dat:.2f}")
        print(f"Recall: {recall_dat:.2f}")
        print("Confusion Matrix:")
        print(cm_dat)

        print("NET:")
        print(f"Precision: {precision_net:.2f}")
        print(f"Recall: {recall_net:.2f}")
        print("Confusion Matrix:")
        print(cm_net)

        # Chaos Plot
        plt.figure()
        plt.scatter(self.test_labels_dat, y_pred_prob_dat, alpha=0.5, color='darkorange', label='DAT')
        plt.scatter(self.test_labels_net, y_pred_prob_net, alpha=0.5, color='darkblue', label='NET')
        plt.xlabel('Actual Labels')
        plt.ylabel('Predicted Probabilities')
        plt.title('Chaos Plot')
        plt.legend(loc="upper left")
        plt.show()

        # Save the model
        self.model.save("learning_data_01.h5")

    def predict(self, input_data):
        if self.model is None:
            raise ValueError("Model is not trained. Please train the model first.")
        
        input_data_scaled = self.scaler.transform(input_data)
        return self.model.predict(input_data_scaled)

    def predict_cocaine_and_amphetamine(self):
        # Get the SMILES for cocaine and D-amphetamine
        cocaine_smiles = 'COC(=O)C1C(OC(=O)C2=CC=CC=C2)CC2CCC1N2C'
        amphetamine_smiles = 'CC(N)CC1=CC=CC=C1'

        # Convert SMILES to molecular descriptors
        cocaine_descriptor = compute_descriptors(cocaine_smiles)
        amphetamine_descriptor = compute_descriptors(amphetamine_smiles)

        # Predict IC50 values for DAT and NET
        cocaine_prediction = self.predict(np.array([cocaine_descriptor]))
        amphetamine_prediction = self.predict(np.array([amphetamine_descriptor]))

        print("Cocaine IC50 Prediction:")
        print(f"DAT: {cocaine_prediction[0, 0]:.2f}")
        print(f"NET: {cocaine_prediction[0, 1]:.2f}")

        print("D-Amphetamine IC50 Prediction:")
        print(f"DAT: {amphetamine_prediction[0, 0]:.2f}")
        print(f"NET: {amphetamine_prediction[0, 1]:.2f}")

    def compare_models(self):
        # Train a Random Forest model
        rf_model = RandomForestClassifier(n_estimators=100, random_state=42)
        rf_model.fit(np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0),
                     np.column_stack((self.train_labels_dat, self.train_labels_net)))

        # Evaluate the Random Forest model on the test data
        rf_y_pred_prob_dat = rf_model.predict_proba(self.test_data_scaled_dat)[:, 1]
        rf_y_pred_prob_net = rf_model.predict_proba(self.test_data_scaled_net)[:, 1]
        rf_y_pred_classes_dat = rf_model.predict(self.test_data_scaled_dat)
        rf_y_pred_classes_net = rf_model.predict(self.test_data_scaled_net)

        # ROC AUC comparison
        nn_fpr_dat, nn_tpr_dat, _ = roc_curve(self.test_labels_dat, self.model.predict(self.test_data_scaled_dat)[:, 0])
        nn_roc_auc_dat = auc(nn_fpr_dat, nn_tpr_dat)

        nn_fpr_net, nn_tpr_net, _ = roc_curve(self.test_labels_net, self.model.predict(self.test_data_scaled_net)[:, 1])
        nn_roc_auc_net = auc(nn_fpr_net, nn_tpr_net)

        rf_fpr_dat, rf_tpr_dat, _ = roc_curve(self.test_labels_dat, rf_y_pred_prob_dat)
        rf_roc_auc_dat = auc(rf_fpr_dat, rf_tpr_dat)

        rf_fpr_net, rf_tpr_net, _ = roc_curve(self.test_labels_net, rf_y_pred_prob_net)
        rf_roc_auc_net = auc(rf_fpr_net, rf_tpr_net)

        plt.figure()
        plt.plot(nn_fpr_dat, nn_tpr_dat, color='darkorange', lw=2, label=f'Neural Network ROC curve for DAT (area = {nn_roc_auc_dat:.2f})')
        plt.plot(nn_fpr_net, nn_tpr_net, color='darkblue', lw=2, label=f'Neural Network ROC curve for NET (area = {nn_roc_auc_net:.2f})')
        plt.plot(rf_fpr_dat, rf_tpr_dat, color='red', lw=2 label=f'Random Forest ROC curve for DAT (area = {rf_roc_auc_dat:.2f})')
        plt.plot(rf_fpr_net, rf_tpr_net, color='green', lw=2, label=f'Random Forest ROC curve for NET (area = {rf_roc_auc_net:.2f})')
        plt.plot([0, 1], [0, 1], color='navy', lw=2, linestyle='--')
        plt.xlim([0.0, 1.0])
        plt.ylim([0.0, 1.05])
        plt.xlabel('False Positive Rate')
        plt.ylabel('True Positive Rate')
        plt.title('Receiver Operating Characteristic Comparison')
        plt.legend(loc="lower right")
        plt.show()

        # Precision, Recall, and F1-score comparison
        nn_precision_dat = precision_score(self.test_labels_dat, (self.model.predict(self.test_data_scaled_dat)[:, 0] > 0.5).astype(int))
        nn_recall_dat = recall_score(self.test_labels_dat, (self.model.predict(self.test_data_scaled_dat)[:, 0] > 0.5).astype(int))
        nn_f1_dat = 2 * (nn_precision_dat * nn_recall_dat) / (nn_precision_dat + nn_recall_dat)

        nn_precision_net = precision_score(self.test_labels_net, (self.model.predict(self.test_data_scaled_net)[:, 1] > 0.5).astype(int))
        nn_recall_net = recall_score(self.test_labels_net, (self.model.predict(self.test_data_scaled_net)[:, 1] > 0.5).astype(int))
        nn_f1_net = 2 * (nn_precision_net * nn_recall_net) / (nn_precision_net + nn_recall_net)

        rf_precision_dat = precision_score(self.test_labels_dat, rf_y_pred_classes_dat)
        rf_recall_dat = recall_score(self.test_labels_dat, rf_y_pred_classes_dat)
        rf_f1_dat = 2 * (rf_precision_dat * rf_recall_dat) / (rf_precision_dat + rf_recall_dat)

        rf_precision_net = precision_score(self.test_labels_net, rf_y_pred_classes_net)
        rf_recall_net = recall_score(self.test_labels_net, rf_y_pred_classes_net)
        rf_f1_net = 2 * (rf_precision_net * rf_recall_net) / (rf_precision_net + rf_recall_net)

        print("Neural Network (DAT):")
        print(f"Precision: {nn_precision_dat:.2f}")
        print(f"Recall: {nn_recall_dat:.2f}")
        print(f"F1-score: {nn_f1_dat:.2f}")

        print("Neural Network (NET):")
        print(f"Precision: {nn_precision_net:.2f}")
        print(f"Recall: {nn_recall_net:.2f}")
        print(f"F1-score: {nn_f1_net:.2f}")

        print("Random Forest (DAT):")
        print(f"Precision: {rf_precision_dat:.2f}")
        print(f"Recall: {rf_recall_dat:.2f}")
        print(f"F1-score: {rf_f1_dat:.2f}")

        print("Random Forest (NET):")
        print(f"Precision: {rf_precision_net:.2f}")
        print(f"Recall: {rf_recall_net:.2f}")
        print(f"F1-score: {rf_f1_net:.2f}")

    def visualize_feature_importance(self):
        # Train a Random Forest model
        rf_model = RandomForestClassifier(n_estimators=100, random_state=42)
        rf_model.fit(np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0),
                     np.column_stack((self.train_labels_dat, self.train_labels_net)))

        # Perform permutation feature importance
        result_dat = permutation_importance(rf_model, self.test_data_scaled_dat, self.test_labels_dat, n_repeats=10, random_state=42)
        result_net = permutation_importance(rf_model, self.test_data_scaled_net, self.test_labels_net, n_repeats=10, random_state=42)
        
        # Sort the feature importances
        sorted_idx_dat = result_dat.importances_mean.argsort()
        sorted_idx_net = result_net.importances_mean.argsort()

        # Plot the feature importances for DAT
        plt.figure(figsize=(10, 8))
        plt.subplot(2, 1, 1)
        plt.barh(range(self.test_data_dat.shape[1]), result_dat.importances_mean[sorted_idx_dat], align='center')
        plt.yticks(range(self.test_data_dat.shape[1]), [f"Feature {i+1}" for i in sorted_idx_dat])
        plt.xlabel("Feature Importance for DAT")
        plt.ylabel("Feature")
        plt.title("Permutation Feature Importance")

        # Plot the feature importances for NET
        plt.subplot(2, 1, 2)
        plt.barh(range(self.test_data_net.shape[1]), result_net.importances_mean[sorted_idx_net], align='center')
        plt.yticks(range(self.test_data_net.shape[1]), [f"Feature {i+1}" for i in sorted_idx_net])
        plt.xlabel("Feature Importance for NET")
        plt.ylabel("Feature")
        plt.tight_layout()
        plt.show()

    def evaluate_on_external_data(self, external_data, external_labels_dat, external_labels_net):
        external_data_scaled = self.scaler.transform(external_data)
        y_pred_prob_dat = self.model.predict(external_data_scaled)[:, 0]
        y_pred_prob_net = self.model.predict(external_data_scaled)[:, 1]
        y_pred_classes_dat = (y_pred_prob_dat > 0.5).astype(int)
        y_pred_classes_net = (y_pred_prob_net > 0.5).astype(int)
        
        precision_dat = precision_score(external_labels_dat, y_pred_classes_dat)
        recall_dat = recall_score(external_labels_dat, y_pred_classes_dat)
        f1_dat = 2 * (precision_dat * recall_dat) / (precision_dat + recall_dat)

        precision_net = precision_score(external_labels_net, y_pred_classes_net)
        recall_net = recall_score(external_labels_net, y_pred_classes_net)
        f1_net = 2 * (precision_net * recall_net) / (precision_net + recall_net)

        print("Evaluation on External Data (DAT):")
        print(f"Precision: {precision_dat:.2f}")
        print(f"Recall: {recall_dat:.2f}")
        print(f"F1-score: {f1_dat:.2f}")

        print("Evaluation on External Data (NET):")
        print(f"Precision: {precision_net:.2f}")
        print(f"Recall: {recall_net:.2f}")
        print(f"F1-score: {f1_net:.2f}")

    def load_model(self, model_path):
        self.model = load_model(model_path)

    def interpret_model(self, sample_data):
        # Create a DeepExplainer object
        explainer = shap.DeepExplainer(self.model, np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0))

        # Compute Shapley values for the sample data
        shap_values = explainer.shap_values(sample_data)

        # Plot the Shapley values for each sample
        plt.figure(figsize=(10, 8))
        plt.subplot(2, 1, 1)
        shap.summary_plot(shap_values[0], sample_data, plot_type="bar", feature_names=[f"Feature {i+1}" for i in range(sample_data.shape[1])], title="DAT")
        plt.subplot(2, 1, 2)
        shap.summary_plot(shap_values[1], sample_data, plot_type="bar", feature_names=[f"Feature {i+1}" for i in range(sample_data.shape[1])], title="NET")
        plt.tight_layout()
        plt.show()

    def plot_learning_curve(self):
        # Compute the learning curve
        train_sizes, train_scores, test_scores = learning_curve(
            self.create_model(**self.best_params),
            np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0),
            np.column_stack((self.train_labels_dat, self.train_labels_net)),
            cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42),
            scoring='precision',
            train_sizes=np.linspace(0.1, 1.0, 10),
            n_jobs=-1
        )

        # Compute the mean and standard deviation of the scores
        train_scores_mean = np.mean(train_scores, axis=1)
        train_scores_std = np.std(train_scores, axis=1)
        test_scores_mean = np.mean(test_scores, axis=1)
        test_scores_std = np.std(test_scores, axis=1)

        # Plot the learning curve
        plt.figure(figsize=(10, 8))
        plt.fill_between(train_sizes, train_scores_mean - train_scores_std, train_scores_mean + train_scores_std, alpha=0.1, color="r")
        plt.fill_between(train_sizes, test_scores_mean - test_scores_std, test_scores_mean + test_scores_std, alpha=0.1, color="g")
        plt.plot(train_sizes, train_scores_mean, 'o-', color="r", label="Training score")
        plt.plot(train_sizes, test_scores_mean, 'o-', color="g", label="Cross-validation score")
        plt.xlabel("Training examples")
        plt.ylabel("Score")
        plt.title("Learning Curve")
        plt.legend(loc="best")
        plt.show()

    def visualize_hyperparameter_importance(self, study):
        # Plot the hyperparameter importance
        plt.figure(figsize=(10, 8))
        optuna.visualization.plot_param_importances(study)
        plt.show()

def compute_descriptors(smiles):
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    
    descriptor_calculator = MolecularDescriptorCalculator([
        'MolWt', 'NumHAcceptors', 'NumHDonors', 'MolLogP', 'TPSA',
        'NumRotatableBonds', 'NumAromaticRings', 'NumAliphaticRings',
        'FractionCSP3', 'NumHeteroatoms', 'RingCount'
    ])
    
    return np.array(descriptor_calculator.CalcDescriptors(mol))

def load_data_dat():
    target = new_client.target
    target_query = target.search('CHEMBL238')
    targets = pd.DataFrame.from_dict(target_query)
    selected_target = targets.target_chembl_id
    activities = new_client.activity
    res = activities.filter(target_chembl_id='CHEMBL238').filter(standard_type='IC50')
    df = pd.DataFrame.from_dict(res)
    df = df[df.standard_value.notna()]
    data = []
    labels = []
    for _, row in df.iterrows():
        smiles = row['canonical_smiles']
        descriptors = compute_descriptors(smiles)
        if descriptors is not None:
            data.append(descriptors)
            labels.append(row['standard_value'])
    return np.array(data), np.array(labels)

def load_data_net():
    target = new_client.target
    target_query = target.search('CHEMBL228')
    targets = pd.DataFrame.from_dict(target_query)
    selected_target = targets.target_chembl_id
    activities = new_client.activity
    res = activities.filter(target_chembl_id='CHEMBL228').filter(standard_type='IC50')
    df = pd.DataFrame.from_dict(res)
    df = df[df.standard_value.notna()]
    data = []
    labels = []
    for _, row in df.iterrows():
        smiles = row['canonical_smiles']
        descriptors = compute_descriptors(smiles)
        if descriptors is not None:
            data.append(descriptors)
            labels.append(row['standard_value'])
    return np.array(data), np.array(labels)

# Usage example
data_dat, labels_dat = load_data_dat()
data_net, labels_net = load_data_net()

predictor = IC50Predictor(data_dat, labels_dat, data_net, labels_net)

# Hyperparameter optimization using Optuna
best_params_optuna, study = predictor.optuna_optimize(n_trials=100)

# Hyperparameter optimization using Grid Search
best_params_grid = predictor.grid_search_optimize()

# Train and evaluate the model using the best parameters from Optuna
predictor.best_params = best_params_optuna
predictor.train_and_evaluate(best_params_optuna)

# Predict IC50 for cocaine and D-amphetamine
predictor.predict_cocaine_and_amphetamine()

# Compare the neural network model with a Random Forest model
predictor.compare_models()

# Visualize feature importance using permutation importance
predictor.visualize_feature_importance()

# Evaluate the model on external test data
external_data, external_labels_dat, external_labels_net = load_external_data()  # Implement the load_external_data function
predictor.evaluate_on_external_data(external_data, external_labels_dat, external_labels_net)

# Load a saved model
predictor.load_model("learning_data_01.h5")

# Interpret the model using Shapley values
sample_data = predictor.test_data_scaled_dat[:10]  # Select a subset of test data for interpretation
predictor.interpret_model(sample_data)

# Plot the learning curve
predictor.plot_learning_curve()

# Visualize hyperparameter importance
predictor.visualize_hyperparameter_importance(study)
plt.plot(rf_fpr_net, rf_tpr_net, color='green', lw=2, label=f'Random Forest ROC curve for NET (area = {rf_roc_auc_net:.2f})')
        plt.plot([0, 1], [0, 1], color='navy', lw=2, linestyle='--')
        plt.xlim([0.0, 1.0])
        plt.ylim([0.0, 1.05])
        plt.xlabel('False Positive Rate')
        plt.ylabel('True Positive Rate')
        plt.title('Receiver Operating Characteristic Comparison')
        plt.legend(loc="lower right")
        plt.show()

        # Precision, Recall, and F1-score comparison
        nn_precision_dat = precision_score(self.test_labels_dat, (self.model.predict(self.test_data_scaled_dat)[:, 0] > 0.5).astype(int))
        nn_recall_dat = recall_score(self.test_labels_dat, (self.model.predict(self.test_data_scaled_dat)[:, 0] > 0.5).astype(int))
        nn_f1_dat = 2 * (nn_precision_dat * nn_recall_dat) / (nn_precision_dat + nn_recall_dat)

        nn_precision_net = precision_score(self.test_labels_net, (self.model.predict(self.test_data_scaled_net)[:, 1] > 0.5).astype(int))
        nn_recall_net = recall_score(self.test_labels_net, (self.model.predict(self.test_data_scaled_net)[:, 1] > 0.5).astype(int))
        nn_f1_net = 2 * (nn_precision_net * nn_recall_net) / (nn_precision_net + nn_recall_net)

        rf_precision_dat = precision_score(self.test_labels_dat, rf_y_pred_classes_dat)
        rf_recall_dat = recall_score(self.test_labels_dat, rf_y_pred_classes_dat)
        rf_f1_dat = 2 * (rf_precision_dat * rf_recall_dat) / (rf_precision_dat + rf_recall_dat)

        rf_precision_net = precision_score(self.test_labels_net, rf_y_pred_classes_net)
        rf_recall_net = recall_score(self.test_labels_net, rf_y_pred_classes_net)
        rf_f1_net = 2 * (rf_precision_net * rf_recall_net) / (rf_precision_net + rf_recall_net)

        print("Neural Network (DAT):")
        print(f"Precision: {nn_precision_dat:.2f}")
        print(f"Recall: {nn_recall_dat:.2f}")
        print(f"F1-score: {nn_f1_dat:.2f}")

        print("Neural Network (NET):")
        print(f"Precision: {nn_precision_net:.2f}")
        print(f"Recall: {nn_recall_net:.2f}")
        print(f"F1-score: {nn_f1_net:.2f}")

        print("Random Forest (DAT):")
        print(f"Precision: {rf_precision_dat:.2f}")
        print(f"Recall: {rf_recall_dat:.2f}")
        print(f"F1-score: {rf_f1_dat:.2f}")

        print("Random Forest (NET):")
        print(f"Precision: {rf_precision_net:.2f}")
        print(f"Recall: {rf_recall_net:.2f}")
        print(f"F1-score: {rf_f1_net:.2f}")

    def visualize_feature_importance(self):
        # Train a Random Forest model
        rf_model = RandomForestClassifier(n_estimators=100, random_state=42)
        rf_model.fit(np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0),
                     np.column_stack((self.train_labels_dat, self.train_labels_net)))

        # Perform permutation feature importance
        result_dat = permutation_importance(rf_model, self.test_data_scaled_dat, self.test_labels_dat, n_repeats=10, random_state=42)
        result_net = permutation_importance(rf_model, self.test_data_scaled_net, self.test_labels_net, n_repeats=10, random_state=42)
        
        # Sort the feature importances
        sorted_idx_dat = result_dat.importances_mean.argsort()
        sorted_idx_net = result_net.importances_mean.argsort()

        # Plot the feature importances for DAT
        plt.figure(figsize=(10, 8))
        plt.subplot(2, 1, 1)
        plt.barh(range(self.test_data_dat.shape[1]), result_dat.importances_mean[sorted_idx_dat], align='center')
        plt.yticks(range(self.test_data_dat.shape[1]), [f"Feature {i+1}" for i in sorted_idx_dat])
        plt.xlabel("Feature Importance for DAT")
        plt.ylabel("Feature")
        plt.title("Permutation Feature Importance")

        # Plot the feature importances for NET
        plt.subplot(2, 1, 2)
        plt.barh(range(self.test_data_net.shape[1]), result_net.importances_mean[sorted_idx_net], align='center')
        plt.yticks(range(self.test_data_net.shape[1]), [f"Feature {i+1}" for i in sorted_idx_net])
        plt.xlabel("Feature Importance for NET")
        plt.ylabel("Feature")
        plt.tight_layout()
        plt.show()

    def evaluate_on_external_data(self, external_data, external_labels_dat, external_labels_net):
        external_data_scaled = self.scaler.transform(external_data)
        y_pred_prob_dat = self.model.predict(external_data_scaled)[:, 0]
        y_pred_prob_net = self.model.predict(external_data_scaled)[:, 1]
        y_pred_classes_dat = (y_pred_prob_dat > 0.5).astype(int)
        y_pred_classes_net = (y_pred_prob_net > 0.5).astype(int)
        
        precision_dat = precision_score(external_labels_dat, y_pred_classes_dat)
        recall_dat = recall_score(external_labels_dat, y_pred_classes_dat)
        f1_dat = 2 * (precision_dat * recall_dat) / (precision_dat + recall_dat)

        precision_net = precision_score(external_labels_net, y_pred_classes_net)
        recall_net = recall_score(external_labels_net, y_pred_classes_net)
        f1_net = 2 * (precision_net * recall_net) / (precision_net + recall_net)

        print("Evaluation on External Data (DAT):")
        print(f"Precision: {precision_dat:.2f}")
        print(f"Recall: {recall_dat:.2f}")
        print(f"F1-score: {f1_dat:.2f}")

        print("Evaluation on External Data (NET):")
        print(f"Precision: {precision_net:.2f}")
        print(f"Recall: {recall_net:.2f}")
        print(f"F1-score: {f1_net:.2f}")

    def load_model(self, model_path):
        self.model = load_model(model_path)

    def interpret_model(self, sample_data):
        # Create a DeepExplainer object
        explainer = shap.DeepExplainer(self.model, np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0))

        # Compute Shapley values for the sample data
        shap_values = explainer.shap_values(sample_data)

        # Plot the Shapley values for each sample
        plt.figure(figsize=(10, 8))
        plt.subplot(2, 1, 1)
        shap.summary_plot(shap_values[0], sample_data, plot_type="bar", feature_names=[f"Feature {i+1}" for i in range(sample_data.shape[1])], title="DAT")
        plt.subplot(2, 1, 2)
        shap.summary_plot(shap_values[1], sample_data, plot_type="bar", feature_names=[f"Feature {i+1}" for i in range(sample_data.shape[1])], title="NET")
        plt.tight_layout()
        plt.show()

    def plot_learning_curve(self):
        # Compute the learning curve
        train_sizes, train_scores, test_scores = learning_curve(
            self.create_model(**self.best_params),
            np.concatenate((self.train_data_scaled_dat, self.train_data_scaled_net), axis=0),
            np.column_stack((self.train_labels_dat, self.train_labels_net)),
            cv=StratifiedKFold(n_splits=5, shuffle=True, random_state=42),
            scoring='precision',
            train_sizes=np.linspace(0.1, 1.0, 10),
            n_jobs=-1
        )

        # Compute the mean and standard deviation of the scores
        train_scores_mean = np.mean(train_scores, axis=1)
        train_scores_std = np.std(train_scores, axis=1)
        test_scores_mean = np.mean(test_scores, axis=1)
        test_scores_std = np.std(test_scores, axis=1)

        # Plot the learning curve
        plt.figure(figsize=(10, 8))
        plt.fill_between(train_sizes, train_scores_mean - train_scores_std, train_scores_mean + train_scores_std, alpha=0.1, color="r")
        plt.fill_between(train_sizes, test_scores_mean - test_scores_std, test_scores_mean + test_scores_std, alpha=0.1, color="g")
        plt.plot(train_sizes, train_scores_mean, 'o-', color="r", label="Training score")
        plt.plot(train_sizes, test_scores_mean, 'o-', color="g", label="Cross-validation score")
        plt.xlabel("Training examples")
        plt.ylabel("Score")
        plt.title("Learning Curve")
        plt.legend(loc="best")
        plt.show()

    def visualize_hyperparameter_importance(self, study):
        # Plot the hyperparameter importance
        plt.figure(figsize=(10, 8))
        optuna.visualization.plot_param_importances(study)
        plt.show()

def compute_descriptors(smiles):
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    
    descriptor_calculator = MolecularDescriptorCalculator([
        'MolWt', 'NumHAcceptors', 'NumHDonors', 'MolLogP', 'TPSA',
        'NumRotatableBonds', 'NumAromaticRings', 'NumAliphaticRings',
        'FractionCSP3', 'NumHeteroatoms', 'RingCount'
    ])
    
    return np.array(descriptor_calculator.CalcDescriptors(mol))

def load_data_dat():
    target = new_client.target
    target_query = target.search('CHEMBL238')
    targets = pd.DataFrame.from_dict(target_query)
    selected_target = targets.target_chembl_id
    activities = new_client.activity
    res = activities.filter(target_chembl_id='CHEMBL238').filter(standard_type='IC50')
    df = pd.DataFrame.from_dict(res)
    df = df[df.standard_value.notna()]
    data = []
    labels = []
    for _, row in df.iterrows():
        smiles = row['canonical_smiles']
        descriptors = compute_descriptors(smiles)
        if descriptors is not None:
            data.append(descriptors)
            labels.append(row['standard_value'])
    return np.array(data), np.array(labels)

def load_data_net():
    target = new_client.target
    target_query = target.search('CHEMBL228')
    targets = pd.DataFrame.from_dict(target_query)
    selected_target = targets.target_chembl_id
    activities = new_client.activity
    res = activities.filter(target_chembl_id='CHEMBL228').filter(standard_type='IC50')
    df = pd.DataFrame.from_dict(res)
    df = df[df.standard_value.notna()]
    data = []
    labels = []
    for _, row in df.iterrows():
        smiles = row['canonical_smiles']
        descriptors = compute_descriptors(smiles)
        if descriptors is not None:
            data.append(descriptors)
            labels.append(row['standard_value'])
    return np.array(data), np.array(labels)

# Usage example
data_dat, labels_dat = load_data_dat()
data_net, labels_net = load_data_net()

predictor = IC50Predictor(data_dat, labels_dat, data_net, labels_net)

# Hyperparameter optimization using Optuna
best_params_optuna, study = predictor.optuna_optimize(n_trials=100)

# Hyperparameter optimization using Grid Search
best_params_grid = predictor.grid_search_optimize()

# Train and evaluate the model using the best parameters from Optuna
predictor.best_params = best_params_optuna
predictor.train_and_evaluate(best_params_optuna)

# Predict IC50 for cocaine and D-amphetamine
predictor.predict_cocaine_and_amphetamine()

# Compare the neural network model with a Random Forest model
predictor.compare_models()

# Visualize feature importance using permutation importance
predictor.visualize_feature_importance()

# Evaluate the model on external test data
external_data, external_labels_dat, external_labels_net = load_external_data()  # Implement the load_external_data function
predictor.evaluate_on_external_data(external_data, external_labels_dat, external_labels_net)

# Load a saved model
predictor.load_model("learning_data_01.h5")

# Interpret the model using Shapley values
sample_data = predictor.test_data_scaled_dat[:10]  # Select a subset of test data for interpretation
predictor.interpret_model(sample_data)

# Plot the learning curve
predictor.plot_learning_curve()

# Visualize hyperparameter importance
predictor.visualize_hyperparameter_importance(study)
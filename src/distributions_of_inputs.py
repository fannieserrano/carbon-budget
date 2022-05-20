import os
import numpy as np
import pandas as pd
import scipy.stats
import scipy.interpolate


def tcre_distribution(low, high, likelihood, n_return, tcre_dist):
    """

    :param low: float
        The lower limit of the probability distribution
    :param high: float
        The upper limit of the probability distribution. Must be larger than low. 
    :param likelihood: float
        Probability (between 0 and 1) that the value will be between low and high
    :param n_return: int
        The number of values to return
    :param tcre_dist: Str
        Either "normal" for a normal distribution, "lognormal" for a lognormal
        distribution, or "lognormal mean match", for a lognormal but with the same mean
        and standard deviation as a normal distribution fitting the same
        high/low/likelihood values.
    :return:
    """
    assert high > low, "High and low limits are the wrong way around"
    if tcre_dist == "normal":
        # We want a normal distribution such that we are between low and high with a
        # given likelihood.
        mean = (high + low) / 2
        # find the normalised z score that gives a normal cdf with given likelihood of
        # being between the required high and low values
        z = scipy.stats.norm.ppf((1 + likelihood) / 2)
        sd = (high - mean) / z
        return np.random.normal(mean, sd, n_return)
    elif tcre_dist == "lognormal mean match":
        mean = (high + low) / 2
        assert mean > 0, "lognormal distributions are always positive"
        z = scipy.stats.norm.ppf((1 + likelihood) / 2)
        sd = (high - mean) / z
        # The lognormal function takes arguments of the underlying mu and sigma values,
        # which are not the same as the actual mean and s.d., so we convert below
        # Derive relations from: mean = exp(\mu + \sigma^2/2),
        # sd = (exp(\sigma^2) - 1)^0.5 * exp(\mu + \sigma^2/2)
        sigma = (np.log(1 + (sd / mean) ** 2)) ** 0.5
        mu = np.log(mean) - sigma ** 2 / 2
        return np.random.lognormal(mean=mu, sigma=sigma, size=n_return)
    elif tcre_dist == "lognormal":
        assert high > 0
        assert low > 0
        # We have h = exp(\mu + \sigma z), l = exp(\mu - \sigma z) ,
        # for z normally distributed as before. Rearranging and solving:
        z = scipy.stats.norm.ppf((1 + likelihood) / 2)
        mu = 0.5 * np.log(low * high)
        sigma = 0.5 * np.log(high / low)
        return np.random.lognormal(mean=mu, sigma=sigma, size=n_return)
    # If you haven't returned yet, something went wrong.
    raise ValueError(
        "tcre_dist must be either normal, lognormal mean match or lognormal, it was {}".format(
            tcre_dist
        )
    )


def establish_median_temp_dep_linear(models, temps, quantile):
    """
    Calculates the median line of fit
    :param models: pd.Database.
        Must include a column marked 'quantile' with a row 0.5
    :param temps: np.array.
        The temperatures to use
    :param quantile
        The quantile desired (0.5 usually)

    :return: pd.Series
    """
    return pd.Series(
        index=temps,
        data=models["b"].loc[models["quantile"] == quantile].iloc[0] * temps
        + models["a"].loc[models["quantile"] == quantile].iloc[0],
    )

def establish_median_temp_dep_nonlinear(models, temps, quantile):
    """
    Calculates the median line of fit
    :param models: pd.Database.
        Must include a column with the name of the quantile and sorted ascending xs
    :param temps: np.array.
        The temperatures to use
    :param quantile
        The quantile desired (0.5 usually)

    :return: pd.Series
    """
    return pd.Series(
        index=temps,
        data=scipy.interpolate.interp1d(
            models["x"],
            models[quantile].squeeze(),
            bounds_error=False,
            fill_value=(models[quantile].values[0], models[quantile].values[-1]),
        )(temps)
    )


def establish_least_sq_temp_dependence(db, temps, non_co2_col, temp_col):
    regres = np.polyfit(db[temp_col], db[non_co2_col], 1)
    return pd.Series(index=temps, data=temps * regres[0] + regres[1])


def load_data_from_MAGICC(
    non_co2_magicc_file,
    tot_magicc_file,
    yearfile,
    non_co2_col,
    tot_col,
    magicc_nonco2_temp_variable,
    tot_temp_variable,
    offset_years,
    peak_version=None,
    permafrost=None,
    vetted_scen_list_file=None,
    vetted_scen_list_file_sheet=None,
    sr15_rename=False
):
    """
    Loads the non-CO2 warming and total warming from files in the format output by
    MAGICC.
    :param non_co2_magicc_file: The file location for non-CO2 warming
    :param tot_magicc_file: The file for total warming
    :param yearfile: The file where emissions are recorded
    :param non_co2_col: The name of the non-CO2 warming column in the output
    :param tot_col: The name of the total warming column in the output df
    :param magicc_nonco2_temp_variable: The name of the non-CO2 warming variable to be
        investigated
    :param tot_temp_variable: The name of the total warming variable to be investigated
    :param offset_years: The years over which the baseline average should be taken
    :param peak_version: String describing which year the non-CO2 warming should be
        taken in.
        Default = None; ignore scenarios with non-peaking cumulative CO2 emissions, use
        non-CO2 warming in the year of peak cumulative CO2.
        If "peakNonCO2Warming", we use the highest non-CO2 temperature,
        irrespective of emissions peak.
        If "nonCO2AtPeakTot", computes the non-CO2 component at the time of peak total
        temperature.
    :param permafrost: Boolean indicating whether or not the data should include
        permafrost corrections. If none (default) all data is accepted from the files
        read. Otherwise the files must have a permafrost column for this to filter.
    :param vetted_scen_list_file: excel file with the list of accepted model/scenario
        combos.
    :param sr15_rename: Boolean, indicates whether or not the model and scenario names
        in the vetted_scen_list_file need to be corrected to align with the names used
        in the other files.

    :return: pd.Dataframe
    """
    yeardf = pd.read_csv(yearfile, index_col=0)
    # Drop empty columns from the dataframe and calculate the year the emissions go to
    # zero
    if vetted_scen_list_file:
        nzyearcol = "year of netzero CO2 emissions"
        vetted_scens = pd.read_excel(
            vetted_scen_list_file, sheet_name=vetted_scen_list_file_sheet
        ).loc[:, ["model", "scenario", "exclude", nzyearcol]]
        assert all(vetted_scens.exclude == 0)
        if peak_version and (peak_version == "officialNZ"):
            vetted_scens_nzyears = vetted_scens
        vetted_scens = vetted_scens.drop(["exclude", nzyearcol], axis=1)
    else:
        vetted_scens = None


    yeardf = _clean_columns_magicc(yeardf, vetted_scens, sr15_rename=sr15_rename)
    empty_cols = [
        col for col in yeardf.columns
        if ((yeardf[col].isnull().all()) or (col == "permafrost"))
    ]
    yeardf.drop(empty_cols, axis=1, inplace=True)
    scenario_cols = ["model", "region", "scenario"]

    del yeardf["unit"]
    total_co2 = yeardf.groupby(scenario_cols).sum()
    if not peak_version:
        total_co2.columns = [int(col[:4]) for col in total_co2.columns]
        zero_years = pd.Series(index=total_co2.index)
        for index, row in total_co2.iterrows():
            try:
                change_sign = np.where(row < 0)[0][0]
                zero_year = scipy.interpolate.interp1d(
                    [row.iloc[change_sign - 1], row.iloc[change_sign]],
                    [row.index[change_sign - 1], row.index[change_sign]],
                )(0)
                zero_years[index] = np.round(zero_year)
            except IndexError:
                zero_years[index] = np.nan
        temp_df = pd.DataFrame(
            index=zero_years.index, columns=[tot_col, non_co2_col], dtype=np.float64
        )
    else:
        temp_df = pd.DataFrame(
            index=total_co2.index, columns=[tot_col, non_co2_col], dtype=np.float64
        )
    # load temperature data and get it into the same format as the emissions data.
    # Do this for both non-CO2 and CO2-only temperature.
    non_co2_df = _read_and_clean_magicc_csv(
        scenario_cols, temp_df, magicc_nonco2_temp_variable, non_co2_magicc_file,
        vetted_scens, use_permafrost=permafrost, sr15_rename=sr15_rename
    )
    tot_df = _read_and_clean_magicc_csv(
        scenario_cols, temp_df, tot_temp_variable, tot_magicc_file,
        vetted_scens, use_permafrost=permafrost, sr15_rename=sr15_rename
    )
    # For each scenario, we subtract the average temperature from the offset years
    for ind, row in tot_df.iterrows():
        temp = max(tot_df.loc[ind])
        temp_df[tot_col][ind] = temp - tot_df.loc[ind][offset_years].mean()

    temp_df["hits_net_zero"] = np.nan
    for ind, row in non_co2_df.iterrows():
        if not peak_version:
            zero_year = zero_years.loc[ind]
            if not np.isnan(zero_year):
                temp = non_co2_df.loc[ind][zero_year]
                temp_df.loc[ind, "hits_net_zero"] = zero_year
            else:
                temp = non_co2_df.loc[ind][2100]

        elif peak_version == "peakNonCO2Warming":
            temp = max(non_co2_df.loc[ind])
            temp_df.loc[ind, "hits_net_zero"] = non_co2_df.columns[np.argmax(non_co2_df.loc[ind])]
        elif peak_version == "nonCO2AtPeakTot":
            max_year_ind = np.where(max(tot_df.loc[ind]) == tot_df.loc[ind])[0]
            temp = non_co2_df.loc[ind].iloc[max_year_ind].values[0]
            temp_df.loc[ind, "hits_net_zero"] = tot_df.columns[max_year_ind]
        elif peak_version == "officialNZ":
            max_year = vetted_scens_nzyears.loc[
                (vetted_scens_nzyears.model == ind[0]) & (vetted_scens_nzyears.scenario == ind[2])
            ][nzyearcol].iloc[0]

            if not np.isnan(max_year):
                temp = non_co2_df.loc[ind, max_year]
                temp_df.loc[ind, "hits_net_zero"] = max_year
            else:
                temp = non_co2_df.loc[ind, 2100]
        else:
            raise ValueError("Invalid choice for peak_version {}".format(peak_version))
        temp_df.loc[ind, non_co2_col] = temp - non_co2_df.loc[ind][offset_years].mean()

    return temp_df


def rename_scenario_to_sr15_names(s):
    replacements = (
        ("SocioeconomicFactorCM", "SFCM"),
        ("TransportERL", "TERL"),
    )
    out = s

    for old, new in replacements:
        out = out.replace(old, new)

    return out


def rename_model_to_sr15_names(m):
    replacements = (
        ("AIM_", "AIM/CGE "),
        ("2_0", "2.0"),
        ("2_1", "2.1"),
        ("5_005", "5.005"),
        ("GCAM_4_2", "GCAM 4.2"),
        ("WEM", "IEA World Energy Model 2017"),
        ("IMAGE_", "IMAGE "),
        ("3_0_1", "3.0.1"),
        ("3_0_2", "3.0.2"),
        ("MERGE-ETL_6_0", "MERGE-ETL 6.0"),
        ("MESSAGE-GLOBIOM_1_0", "MESSAGE-GLOBIOM 1.0"),
        ("MESSAGE_V_3", "MESSAGE V.3"),
        ("MESSAGEix-GLOBIOM_1_0", "MESSAGEix-GLOBIOM 1.0"),
        ("POLES_", "POLES "),
        ("CDL", "CD-LINKS"),
        ("REMIND_", "REMIND "),
        ("REMIND-MAgPIE_", "REMIND-MAgPIE "),
        ("1_5", "1.5"),
        ("1_7", "1.7"),
        ("3_0", "3.0"),
        ("WITCH-GLOBIOM_", "WITCH-GLOBIOM "),
        ("3_1", "3.1"),
        ("4_2", "4.2"),
        ("4_4", "4.4"),
    )
    out = m

    for old, new in replacements:
        out = out.replace(old, new)

    return out


def _clean_columns_magicc(df, vetted_scens, sr15_rename):
    to_drop_cols = [
        "Unnamed: 0", "Category", "Category_name",
        "Exceedance Probability 1.5C (MAGICCv7.5.3)",
        "Exceedance Probability 2.0C (MAGICCv7.5.3)",
        "Exceedance Probability 2.5C (MAGICCv7.5.3)",
        "Exceedance Probability 3.0C (MAGICCv7.5.3)", "climate-models", "exclude",
        "median peak warming (MAGICCv7.5.3)",
        "median warming in 2100 (MAGICCv7.5.3)",
        "median year of peak warming (MAGICCv7.5.3)",
        "p33 peak warming (MAGICCv7.5.3)", "p33 warming in 2100 (MAGICCv7.5.3)",
        "p33 year of peak warming (MAGICCv7.5.3)", "p67 peak warming (MAGICCv7.5.3)",
        "p67 warming in 2100 (MAGICCv7.5.3)", "p67 year of peak warming (MAGICCv7.5.3)",
        "Exceedance Probability 1.5C|MAGICCv7.5.1",
        "Exceedance Probability 2.0C|MAGICCv7.5.1",
        "Exceedance Probability 2.5C|MAGICCv7.5.1",
        "Exceedance Probability 3.0C|MAGICCv7.5.1", "climate-models", "exclude",
        "harmonization", "infilling","median peak warming (MAGICCv7.5.1)",
        "median warming at peak (MAGICCv7.5.1)",
        "median warming in 2100 (MAGICCv7.5.1)",
        "median year of peak warming (MAGICCv7.5.1)", "model_scenario",
        "p67 peak warming (MAGICCv7.5.1)",
        "p67 warming at peak (MAGICCv7.5.1)",
        "p67 warming in 2100 (MAGICCv7.5.1)", "p67 year of peak warming (MAGICCv7.5.1)",
        "pipeline",
        "passes_vetting",
        "year of peak median warming (MAGICCv7.5.1)",
        "year of peak p67 warming (MAGICCv7.5.1)",
        "Vetted",
    ]
    if vetted_scens is not None:
        if sr15_rename:
            df["model"] = df["model"].apply(rename_model_to_sr15_names)
            df["scenario"] = df["scenario"].apply(rename_scenario_to_sr15_names)
        df2 = df.merge(vetted_scens, on=["model", "scenario"], indicator="Vetted", how="outer")
        if any(df2.Vetted != "both"):
            print("Excluding data from scenarios: ")
            print(df2.loc[df2.Vetted != "both"][["model", "scenario"]])
            df2 = df2.loc[df2.Vetted == "both"]
        df = df2
    to_drop_cols = [col for col in to_drop_cols if col in df.columns]
    return df.drop(columns=to_drop_cols)


def _read_and_clean_magicc_csv(
        scenario_cols, temp_df, temp_variable, warmingfile, vetted_scens, use_permafrost=None, sr15_rename=False,
):
    df = pd.read_csv(warmingfile)
    # The following columns may be present and aren't wanted
    # (Unnamed: 0 comes from an untitled index column)
    df = _clean_columns_magicc(df, vetted_scens=vetted_scens, sr15_rename=sr15_rename)
    if use_permafrost is not None:
        if "permafrost" in df.columns:
            df = df[df.permafrost == use_permafrost]
            df = df.drop(columns="permafrost")
        else:
            print("Warning: it's unclear whether the MAGICC file is for permafrost or not")
    elif "permafrost" in df.columns:
        raise ValueError(
            "The input data contains permafrost information but we have not "
            "given instructions with what to do with it."
        )

    df = df.loc[df["variable"] == temp_variable]
    df.set_index(scenario_cols, drop=True, inplace=True)
    del df["unit"]
    del df["variable"]
    assert all([ind in df.index for ind in temp_df.index]), (
        "There is a mismatch between the emissions year file and the temperature "
        "file"
    )
    df = df.loc[[ind for ind in df.index if ind in temp_df.index]]
    df.columns = [int(col[:4]) for col in df.columns]
    return df


def load_data_from_FaIR(
    folder_all,
    folder_co2_only,
    desired_scenarios_db,
    model_col,
    scenario_col,
    magicc_non_co2_col,
    magicc_temp_col,
    offset_years,
    fair_filestr,
    peak_version
):
    import netCDF4
    all_files = os.listdir(folder_all)
    CO2_only_files = os.listdir(folder_co2_only)
    assert all_files == CO2_only_files
    # We must find the correspondence between the file systems and the known years of
    # peak emissions
    desired_scenarios_db["filename"] = fair_filestr + desired_scenarios_db[model_col] + "_" + desired_scenarios_db[scenario_col]
    expected_filenames = desired_scenarios_db["filename"]
    assert len(expected_filenames) == len(
        set(expected_filenames)
    ), "Expected file names are not clearly distinguishable"
    filename_dict = {}
    name_ind = -12 if all_files[0][-3:] == ".nc" else -4
    # Unfortunately the naming convention is quite different between folders so we have to use a
    # horrible gnarl of replacement code to convert between them
    for i in expected_filenames:
        compare_filename = [
            x for x in all_files if (
            x[:name_ind].replace(" ", "_").replace("/", "_").replace(",", "_").replace(
                "_CGE", "").replace(
                "CDL", "CD-LINKS").replace("-", "_").replace(
                "SocioeconomicFactorCM", "SFCM"
                ).replace("TransportERL", "TERL").replace(
                "WEM", "IEA_World_Energy_Model_2017").replace("REMIND_1.5", "REMIND_1_5").replace(".", "_").replace("+", "_").replace(
                "(", "_").replace(")", "_").replace("°", "_").replace("$", "_").replace(
                "__", "_").replace("Npi", "NPI")  == i.replace(" ", "_").replace("/", "_").replace(",", "_").replace(
                "_CGE", "").replace("CDL", "CD-LINKS").replace("-", "_").replace(
                "SocioeconomicFactorCM", "SFCM").replace(
                "TransportERL", "TERL").replace("WEM",
                "IEA_World_Energy_Model_2017").replace("REMIND_1.5", "REMIND_1_5").replace(".", "_").replace(
                "+", "_").replace("(", "_").replace("°", "_").replace(")", "_").replace("$", "_").replace("__", "_").replace("Npi", "NPI")
            )
        ]
        assert len(compare_filename) <= 1, \
            "Processed file names are not clearly distinguishable"
        if len(compare_filename) == 1:
            filename_dict[i] = compare_filename[0]
        else:
            print(f"No match found for {i}")
    temp_all_dbs = []
    temp_only_co2_dbs = []
    desired_inds = []
    for orig, file in filename_dict.items():
        open_link_all = netCDF4.Dataset(folder_all + file)
        open_link_co2_only = netCDF4.Dataset(folder_co2_only + file)
        desired_ind = np.where([x == orig for x in desired_scenarios_db["filename"]])[0][0]
        desired_inds.append(desired_ind)
        if file[-3:] == ".nc":
            var = "temp"
            offset_inds = np.where(
                [y in offset_years for y in open_link_all.variables["time"][:]]
            )[0]
        elif file[-4:] == ".hdf":
            var = "temperature"
            zero_year = 1750
            offset_inds = np.where(
                [y in offset_years for y in
                 range(zero_year, zero_year + open_link_all[var].shape[0])]
            )[0]
        else:
            raise ValueError(f"File {file} in the wrong format")
        assert len(offset_inds) == len(offset_years), \
            "We found the wrong number of offset years in the database."
        selected_date = desired_scenarios_db.loc[desired_ind, "hits_net_zero"]
        if peak_version == "peakNonCO2Warming":
            all_temp_ever = (
                pd.DataFrame(open_link_all[var][:, ::1]).median(axis=1)
            )
            only_co2_temp_ever = (
                pd.DataFrame(open_link_co2_only[var][:, ::1]).median(axis=1)
            )
            time_ind_nonco2 = np.where(
                all_temp_ever-only_co2_temp_ever == max(all_temp_ever-only_co2_temp_ever)
            )[0]
        elif peak_version in [None, "officialNZ"]:
            if file[-3:] == ".nc":
                time_ind_nonco2 = np.where(open_link_all["time"][:] == selected_date)[0]
            else:
                time_ind_nonco2 = int(selected_date - zero_year)
        elif peak_version == "nonCO2AtPeakTot":
            time_ind_nonco2 = np.where(
                pd.DataFrame(open_link_all[var][:]).median(axis=1) == max(pd.DataFrame(
                    open_link_all[var][:]).median(axis=1))
            )[0]
        else:
            raise ValueError(f"peak version {peak_version} not a valid option")
        all_temp = (
            pd.DataFrame(open_link_all[var][:, ::1]).median(axis=1).max()
        )
        all_offset = (
            pd.DataFrame(open_link_all[var][offset_inds, ::1]).mean().median()
        )
        temp_all_dbs.append(all_temp - all_offset)

        only_co2_temp = (
            pd.DataFrame(open_link_co2_only[var][time_ind_nonco2, ::1]).median().median()
        )
        only_co2_offset = (
            pd.DataFrame(open_link_co2_only[var][offset_inds, ::1]).mean().median()
        )
        temp_only_co2_dbs.append(only_co2_temp - only_co2_offset)
        assert (
                all_offset > 0
                and all_temp > 0
                and only_co2_temp > 0
                and only_co2_offset > 0
        ), "Does the database really contain a negative temperature change?"
    temp_no_co2_dbs = np.array(temp_all_dbs) - temp_only_co2_dbs
    assert all(x > 0 for x in temp_all_dbs) and all(
        x > 0 for x in temp_only_co2_dbs
    ), "Does the database really contain a negative temperature change?"
    dbs = pd.DataFrame(
        {magicc_temp_col: temp_all_dbs, magicc_non_co2_col: temp_no_co2_dbs, "magicc_ind": desired_inds},
        dtype="float32",
    )
    return dbs

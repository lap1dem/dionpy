import os
from datetime import datetime

import numpy as np

from .DLayer import DLayer
from .FLayer import FLayer
from .modules.helpers import none_or_array, elaz_mesh
from .modules.ion_tools import trop_refr
from typing import Tuple


class SingleTimeModel:
    def __init__(
            self,
            dt: datetime,
            position: Tuple[float, float, float],
            nside: int = 128,
            dbot: float = 60,
            dtop: float = 90,
            ndlayers: int = 10,
            fbot: float = 150,
            ftop: float = 500,
            nflayers: int = 30,
            autocalc: bool = True,
    ):
        if isinstance(dt, datetime):
            self.dt = dt
        else:
            raise ValueError("Parameter dt must be a datetime object.")
        self.position = position
        self.nside = nside
        self.dlayer = DLayer(
            dt, position, dbot, dtop, ndlayers, nside, autocalc
        )
        self.flayer = FLayer(
            dt, position, fbot, ftop, nflayers, nside, autocalc
        )

    @staticmethod
    def troprefr(el=None):
        """
        Refraction in the troposphere in degrees.
        """
        return np.rad2deg(trop_refr(np.deg2rad(90 - el)))

    def frefr(self, el, az, freq, troposphere=True):
        """
        Calculates refraction in F layer for a given model of ionosphere. Output is the change of zenith angle theta
        (theta -> theta + dtheta). If coordinates are floats the output will be a single number; if they are arrays -
        the output will be a 2D array with dimensions az.size x el.size (according to np.meshgrid(el, az)).

        Parameters
        ----------
        el : None | float | np.ndarray
            Elevation of observation(s). If not provided - the model's grid will be used.
        az : None | float | np.ndarray
            Azimuth of observation(s). If not provided - the model's grid will be used.

        Returns
        -------
        dtheta : float : np.ndarray
            Change in elevation in degrees
        """
        return self.flayer.frefr(el, az, freq, troposphere=troposphere)

    def datten(self, el, az, freq, col_freq="default", troposphere=True):
        """
                Calculates attenuation in D layer for a given model of ionosphere. Output is the attenuation factor between 0
                (total attenuation) and 1 (no attenuation). If coordinates are floats the output will be a single number; if
                they are arrays - the output will be a 2D array with dimensions el.size x az.size.

                Parameters
                ----------
                el : None | float | np.ndarray
                    Elevation of observation(s). If not provided - the model's grid will be used.
                az : None | float | np.ndarray
                    Azimuth of observation(s). If not provided - the model's grid will be used.
                col_freq : str, float
                    The collision frequency ('default', 'nicolet', 'setty', 'aggrawal', or float in Hz)
                troposphere : Bool, default=True
                    Account for troposphere refraction bias

                Returns
                -------
                np.ndarray
        """
        return self.dlayer.datten(el, az, freq, col_freq, troposphere)

    def write_self_to_file(self, file):
        h5dir = f"{self.dt.year:04d}{self.dt.month:02d}{self.dt.day:02d}{self.dt.hour:02d}{self.dt.minute:02d}"
        grp = file.create_group(h5dir)
        meta = grp.create_dataset("meta", shape=(0,))
        meta.attrs["position"] = self.position
        meta.attrs["dt"] = self.dt.strftime("%Y-%m-%d %H:%M")
        meta.attrs["nside"] = self.nside

        meta.attrs["ndlayers"] = self.dlayer.ndlayers
        meta.attrs["dtop"] = self.dlayer.dtop
        meta.attrs["dbot"] = self.dlayer.dbot

        meta.attrs["nflayers"] = self.flayer.nflayers
        meta.attrs["fbot"] = self.flayer.fbot
        meta.attrs["ftop"] = self.flayer.ftop

        if np.average(self.dlayer.d_e_density) > 0 and np.average(self.flayer.f_e_density) > 0:
            grp.create_dataset("d_e_density", data=self.dlayer.d_e_density)
            grp.create_dataset("d_e_temp", data=self.dlayer.d_e_temp)
            grp.create_dataset("f_e_density", data=self.flayer.f_e_density)
            grp.create_dataset("f_e_temp", data=self.flayer.f_e_temp)

    def save(self, directory=None, name=None):
        import h5py
        filename = f"{self.dt.year:04d}{self.dt.month:02d}{self.dt.day:02d}{self.dt.hour:02d}{self.dt.minute:02d}"
        directory = directory or "ion_models/"
        if not os.path.exists(directory):
            os.makedirs(directory)

        name = name or filename
        name = os.path.join(directory, name)
        if not name.endswith(".h5"):
            name += ".h5"

        file = h5py.File(name, mode="w")
        self.write_self_to_file(file)
        file.close()

    @classmethod
    def load(cls, path: str):
        import h5py
        if not path.endswith(".h5"):
            path += ".h5"
        with h5py.File(path, mode="r") as file:
            groups = list(file.keys())
            if len(groups) > 1:
                raise RuntimeError("File contains more than one model. Consider reading it with IonModel class.")

            grp = file[groups[0]]
            meta = grp.get("meta")
            obj = cls(
                autocalc=False,
                dt=datetime.strptime(meta.attrs["dt"], "%Y-%m-%d %H:%M"),
                position=meta.attrs["position"],
                nside=meta.attrs["nside"],

                dbot=meta.attrs["dbot"],
                dtop=meta.attrs["dtop"],
                ndlayers=meta.attrs["ndlayers"],

                fbot=meta.attrs["fbot"],
                ftop=meta.attrs["ftop"],
                nflayers=meta.attrs["nflayers"],
            )
            obj.dlayer.d_e_density = none_or_array(grp.get("d_e_density"))
            obj.dlayer.d_e_temp = none_or_array(grp.get("d_e_temp"))

            obj.flayer.f_e_density = none_or_array(grp.get("f_e_density"))
            obj.flayer.f_e_temp = none_or_array(grp.get("f_e_temp"))
        return obj

    def _polar_plot(
            self,
            data: Tuple[np.ndarray, np.ndarray, np.ndarray],
            title=None,
            barlabel=None,
            plotlabel=None,
            cblim=None,
            saveto=None,
            dpi=300,
            cmap="viridis",
    ):
        import matplotlib.pyplot as plt
        plotlabel = plotlabel or "UTC time: " + datetime.strftime(
            self.dt, "%Y-%m-%d %H:%M"
        )
        cblim = cblim or (np.min(data[2]), np.max(data[2]))

        fig = plt.figure(figsize=(8, 8))
        ax: plt.Axes = fig.add_subplot(111, projection="polar")
        img = ax.pcolormesh(
            data[0],
            data[1],
            data[2],
            cmap=cmap,
            vmin=cblim[0],
            vmax=cblim[1],
            shading="auto",
        )
        ax.grid(color="gray")
        ax.set_rticks([90, 60, 30, 0], Fontsize=30)
        ax.tick_params(axis='both', which='major', labelsize=10)
        ax.scatter(0, 0, c="red", s=5)
        ax.set_theta_zero_location("S")
        plt.colorbar(img, fraction=0.042, pad=0.08).set_label(label=barlabel, size=10)
        plt.title(title, fontsize=14, pad=20)
        plt.xlabel(plotlabel, fontsize=10)

        if saveto is not None:
            head, tail = os.path.split(saveto)
            if not os.path.exists(head):
                os.makedirs(head)
            plt.savefig(saveto, dpi=dpi)
            plt.close(fig)
            return
        return fig

    def plot_ded(
            self,
            gridsize=200,
            layer=None,
            title=None,
            plotlabel=None,
            cblim=None,
            saveto=None,
            dpi=300,
            cmap="viridis",
    ):
        barlabel = r"$m^{-3}$"
        if title is None:
            if layer is None:
                title = r"Average $n_e$ in the D layer"
            else:
                title = r"$n_e$ " + f"in the {layer} sublayer of the D layer"
        el, az = elaz_mesh(gridsize)
        ded = self.dlayer.ded(el, az, layer)
        return self._polar_plot(
            (np.deg2rad(az), 90 - el, ded),
            title,
            barlabel,
            plotlabel,
            cblim,
            saveto,
            dpi,
            cmap,
        )

    def plot_det(
            self,
            gridsize=200,
            layer=None,
            title=None,
            plotlabel=None,
            cblim=None,
            saveto=None,
            dpi=300,
            cmap="viridis",
    ):
        barlabel = r"$K^\circ$"
        if title is None:
            if layer is None:
                title = r"Average $T_e$ in the D layer"
            else:
                title = r"$T_e$ " + f"in the {layer} sublayer of the D layer"
        el, az = elaz_mesh(gridsize)
        det = self.dlayer.det(el, az, layer)
        return self._polar_plot(
            (np.deg2rad(az), 90 - el, det),
            title,
            barlabel,
            plotlabel,
            cblim,
            saveto,
            dpi,
            cmap,
        )

    def plot_fed(
            self,
            gridsize=200,
            layer=None,
            title=None,
            plotlabel=None,
            cblim=None,
            saveto=None,
            dpi=300,
            cmap="viridis",
    ):
        barlabel = r"$m^{-3}$"
        if title is None:
            if layer is None:
                title = r"Average $n_e$ in the F layer"
            else:
                title = r"$n_e$ " + f"in the {layer} sublayer of the F layer"
        el, az = elaz_mesh(gridsize)
        fed = self.flayer.fed(el, az, layer)
        return self._polar_plot(
            (np.deg2rad(az), 90 - el, fed),
            title,
            barlabel,
            plotlabel,
            cblim,
            saveto,
            dpi,
            cmap,
        )

    def plot_fet(
            self,
            gridsize=200,
            layer=None,
            title=None,
            plotlabel=None,
            cblim=None,
            saveto=None,
            dpi=300,
            cmap="viridis",
    ):
        barlabel = r"$K^\circ$"
        if title is None:
            if layer is None:
                title = r"Average $T_e$ in the F layer"
            else:
                title = r"$T_e$ " + f"in the {layer} sublayer of the F layer"
        el, az = elaz_mesh(gridsize)
        fet = self.flayer.fet(el, az, layer)
        return self._polar_plot(
            (np.deg2rad(az), 90 - el, fet),
            title,
            barlabel,
            plotlabel,
            cblim,
            saveto,
            dpi,
            cmap,
        )

    def plot_datten(
            self,
            freq,
            troposphere=True,
            gridsize=200,
            title=None,
            plotlabel=None,
            cblim=None,
            saveto=None,
            dpi=300,
            cmap="viridis",
    ):
        el, az = elaz_mesh(gridsize)
        datten = self.dlayer.datten(el, az, freq, troposphere=troposphere)
        title = title or r"Average $f_{a}$ in the D layer at " + f"{freq / 1e6:.1f} MHz"
        barlabel = None
        return self._polar_plot(
            (np.deg2rad(az), 90 - el, datten),
            title,
            barlabel,
            plotlabel,
            cblim,
            saveto,
            dpi,
            cmap,
        )

    def plot_frefr(
            self,
            freq,
            troposphere=True,
            gridsize=200,
            title=None,
            plotlabel=None,
            cblim=None,
            saveto=None,
            dpi=300,
            cmap="viridis",
    ):
        el, az = elaz_mesh(gridsize)
        frefr = self.flayer.frefr(el, az, freq, troposphere=troposphere)
        title = title or r"Refraction $\delta \theta$ in the F layer at " + f"{freq / 1e6:.1f} MHz"
        barlabel = r"$deg$"
        return self._polar_plot(
            (np.deg2rad(az), 90 - el, frefr),
            title,
            barlabel,
            plotlabel,
            cblim,
            saveto,
            dpi,
            cmap,
        )

    def plot_troprefr(
            self,
            gridsize=200,
            title=None,
            plotlabel=None,
            cblim=None,
            saveto=None,
            dpi=300,
            cmap='viridis_r',
    ):
        el, az = elaz_mesh(gridsize)
        troprefr = self.troprefr(el)
        title = title or r"Refraction $\delta \theta$ in the troposphere"
        barlabel = r"$deg$"
        return self._polar_plot(
            (np.deg2rad(az), 90 - el, troprefr),
            title,
            barlabel,
            plotlabel,
            cblim,
            saveto,
            dpi,
            cmap,
        )

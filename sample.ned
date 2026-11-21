package example_eth_full;

import inet.networks.base.TsnNetworkBase;
import inet.node.tsn.TsnDevice;
import inet.node.tsn.TsnSwitch;
import inet.node.ethernet.Eth100M;
import inet.node.ethernet.Eth10G;
import inet.node.ethernet.Eth1G;

network example_eth_full extends TsnNetworkBase
{
    parameters:
        @display("bgb=992,1100");
        double ethernetPropagationDelay @unit(s) = default(0s);
    submodules:
        SW_Core: TsnSwitch {
            @display("p=132,132");
        }
        SW_ADAS: TsnSwitch {
            @display("p=429,132");
        }
        SW_Front: TsnSwitch {
            @display("p=280,389");
        }
        SW_Rear: TsnSwitch {
            @display("p=-16,389");
        }
        SW_Cabin: TsnSwitch {
            @display("p=-165,132");
        }
        ADAS_HPC: TsnDevice {
            @display("p=726,132");
        }
        FrontCam: TsnDevice {
            @display("p=577,389");
        }
        SurroundCam: TsnDevice {
            @display("p=280,-125");
        }
        Lidar: TsnDevice {
            @display("p=577,-125");
        }
        Radar: TsnDevice {
            @display("p=429,646");
        }
        BrakeCtrl: TsnDevice {
            @display("p=132,646");
        }
        SteeringCtrl: TsnDevice {
            @display("p=792,524");
        }
        Powertrain: TsnDevice {
            @display("p=-165,646");
        }
        RearCam: TsnDevice {
            @display("p=-313,389");
        }
        BodyCtrl: TsnDevice {
            @display("p=118,900");
        }
        HeadUnit: TsnDevice {
            @display("p=-462,132");
        }
        Cluster: TsnDevice {
            @display("p=-313,-125");
        }
        Telematics: TsnDevice {
            @display("p=-16,-125");
        }
        Central_HPC: TsnDevice {
            @display("p=-3,-379");
        }
        Logger: TsnDevice {
            @display("p=267,-379");
        }
    connections:
        SW_Core.ethg++ <--> Eth10G {delay = 15ns;} <--> SW_ADAS.ethg++;
        SW_Core.ethg++ <--> Eth1G {delay = 20ns;} <--> SW_Front.ethg++;
        SW_Core.ethg++ <--> Eth1G {delay = 30ns;} <--> SW_Rear.ethg++;
        SW_Core.ethg++ <--> Eth1G {delay = 15ns;} <--> SW_Cabin.ethg++;
        SW_Front.ethg++ <--> Eth1G {delay = 40ns;} <--> SW_Rear.ethg++;
        SW_Front.ethg++ <--> Eth1G {delay = 10ns;} <--> SW_ADAS.ethg++;
        ADAS_HPC.ethg++ <--> Eth10G {delay = 5ns;} <--> SW_ADAS.ethg++;
        FrontCam.ethg++ <--> Eth1G {delay = 15ns;} <--> SW_ADAS.ethg++;
        SurroundCam.ethg++ <--> Eth1G {delay = 20ns;} <--> SW_ADAS.ethg++;
        Lidar.ethg++ <--> Eth1G {delay = 10ns;} <--> SW_ADAS.ethg++;
        Radar.ethg++ <--> Eth100M {delay = 10ns;} <--> SW_Front.ethg++;
        BrakeCtrl.ethg++ <--> Eth100M {delay = 15ns;} <--> SW_Front.ethg++;
        SteeringCtrl.ethg++ <--> Eth100M {delay = 15ns;} <--> SW_Front.ethg++;
        Powertrain.ethg++ <--> Eth100M {delay = 10ns;} <--> SW_Rear.ethg++;
        RearCam.ethg++ <--> Eth100M {delay = 20ns;} <--> SW_Rear.ethg++;
        BodyCtrl.ethg++ <--> Eth100M {delay = 15ns;} <--> SW_Rear.ethg++;
        HeadUnit.ethg++ <--> Eth1G {delay = 10ns;} <--> SW_Cabin.ethg++;
        Cluster.ethg++ <--> Eth100M {delay = 10ns;} <--> SW_Cabin.ethg++;
        Telematics.ethg++ <--> Eth100M {delay = 15ns;} <--> SW_Cabin.ethg++;
        Central_HPC.ethg++ <--> Eth10G {delay = 5ns;} <--> SW_Core.ethg++;
        Logger.ethg++ <--> Eth1G {delay = 5ns;} <--> SW_Core.ethg++;
}
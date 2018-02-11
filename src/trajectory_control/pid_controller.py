#!/usr/bin/env python

"""
Adapted from Andrea Bajcsy
https://github.com/abajcsy/iact_control/
"""

from enum import Enum
import sys
import time

import numpy as np
from numpy import array
import roslib
import rospy
import kinova_msgs.msg
import geometry_msgs.msg
import std_msgs.msg
import sensor_msgs.msg
import kinova_msgs.srv

from control_msgs.msg import FollowJointTrajectoryFeedback
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

import pid
import ros_utils
import actionlib

roslib.load_manifest('kinova_demo')

PREFIX = 'j2s7s300_driver'

GOAL_EPSILON = 0.005 #if all joints are less than epsilon at goal, then complete, goal is achieved
START_EPSILON = 0.02 #if all joints are less than epsilon at start, the trajectory can begin (prevents jumping)
START_MAX_DIST = 0.5 #if ANY joint is greater than start max distance, throw an error

MAX_CMD_VEL = 40.0 #Maximum commanded velocity

class PIDController(object):
    """
    This class represents a node that moves the Jaco with PID control.
    The joint velocities are computed as:

        V = -K_p(e) - K_d(e_dot) - K_i*Integral(e)
    where:
        e = (target_joint configuration) - (current joint configuration)
        e_dot = derivative of error
        K_p = accounts for present values of position error
        K_i = accounts for past values of error, accumulates error over time
        K_d = accounts for possible future trends of error, based on current rate of change

    Subscribes to:
        /j2s7s300_driver/out/joint_angles	- Jaco sensed joint angles
        /j2s7s300_driver/out/joint_torques	- Jaco sensed joint torques

    Publishes to:
        /j2s7s300_driver/in/joint_velocity	- Jaco commanded joint velocities

    Required parameters:
        p_gain, i_gain, d_gain    - gain terms for the PID controller
        sim_flag 				  - flag for if in simulation or not
    """

    def __init__(self):
        """
        Setup of the ROS node. Publishing computed torques happens at 100Hz.
        """

        self.reached_start = False
        self.reached_goal = False
        self.last_dof = None
        self.target_index = 0
        self.step_size = 1
        self.time_points = None

        # ----- Controller Setup ----- #

        # stores maximum COMMANDED joint torques
        self.max_cmd = MAX_CMD_VEL * np.eye(7)
        # stores current COMMANDED joint torques
        self.cmd = np.eye(7)
        # stores current joint MEASURED joint torques
        self.joint_torques = np.zeros((7, 1))

        # P, I, D gains
        p_gain = 50.0
        i_gain = 0.00
        d_gain = 20.0
        self.P = p_gain * np.eye(7)
        self.I = i_gain * np.eye(7)
        self.D = d_gain * np.eye(7)
        self.controller = pid.PID(self.P, self.I, self.D, 0, 0)

        # ---- ROS Setup ---- #

        rospy.init_node("pid_trajectory_controller", anonymous=True)

        # create joint-velocity publisher
        self.vel_pub = rospy.Publisher(PREFIX + '/in/joint_velocity',
                                       kinova_msgs.msg.JointVelocity,
                                       queue_size=1)



        #subscribe to trajectory  /j2s7s300_driver/trajectory_controller/command
        rospy.Subscriber(PREFIX + '/trajectory_controller/command', JointTrajectory, self.execute_trajectory)
           
        #TODO check if this is publishing on the correct topic
        self.feedback_pub = rospy.Publisher(PREFIX+'trajectory_controller/state', FollowJointTrajectoryFeedback, queue_size=1)
        #pub_joint_feedback_ = nh_.advertise<control_msgs::FollowJointTrajectoryFeedback>("trajectory_controller/state", 1);
        while not rospy.is_shutdown():
            rospy.spin()

    def execute_loop(self):
        r = rospy.Rate(100)

        while not rospy.is_shutdown() and not (self.reached_goal and
                                               self.reached_start):
            #print "sending command", self.cmd
            self.vel_pub.publish(ros_utils.cmd_to_JointVelocityMsg(self.cmd))
            r.sleep()

    def shutdown_controller(self):
        self.joint_sub.unregister()
        self.trajectory = None

    def execute_trajectory(self, traj):
        #traj = JointTrajectory()
        print "got traj", traj
        print "traj.points[0]", traj.points[0]

        # create subscriber to joint_angles
        self.joint_sub = rospy.Subscriber(PREFIX + '/out/joint_angles',
                         kinova_msgs.msg.JointAngles,
                         self.joint_angles_callback, queue_size=1)

        
        #TODO create process traj method
        
        num_points = len(traj.points)
        self.time_points = np.empty(num_points)
        self.time_points[:] = np.nan

        self.trajectory = np.empty((num_points,7))
        self.trajectory[:] = np.nan
        for idx, point in enumerate(traj.points):
            self.trajectory[idx,:] = point.positions
            self.time_points[idx] = point.time_from_start.secs + point.time_from_start.nsecs*1e-9

        print "numpy trajectory", self.trajectory


        #TODO using this function garuntees that distance corresponds to time,
        # I should probably use the time points provided by moveit.
        # time_points will work with any list like structure. 1XN numpy array where
        # N is the number of time points
        print "self.time_points", self.time_points

        # ---- Trajectory Setup ---- #

        # total time for trajectory
        self.trajectory_time = self.time_points[-1]

        self.start = self.trajectory[0].reshape((7, 1))
        self.goal = self.trajectory[-1].reshape((7, 1))
        print "self.start", self.start
        print "self.goal", self.goal

        self.target_pos = self.trajectory[0].reshape((7, 1))
        self.target_index = 0

        # track if you have gotten to start/goal of path
        self.reached_start = False
        self.reached_goal = False

        # keeps running time since beginning of path
        self.path_start_T = time.time()

        self.execute_loop()
        self.shutdown_controller()
        print "******************************************** END Trajectory control"

    def PID_control(self, pos):
        """
        Return a control torque based on PID control
        """
        error = PIDController.shortest_angular_distance(self.target_pos, pos)
        return -self.controller.update_PID(error)

    def joint_angles_callback(self, msg):
        """
        Reads the latest position of the robot and sets an
        appropriate torque command to move the robot to the target
        """
        # read the current joint angles from the robot
        curr_pos = np.array(
            [msg.joint1, msg.joint2, msg.joint3, msg.joint4, msg.joint5,
             msg.joint6, msg.joint7]).reshape((7, 1))

        # convert to radians
        curr_pos = curr_pos * (np.pi / 180.0)

        self.last_dof = curr_pos

        # update target position to move to depending on:
        # - if moving to START of desired trajectory or
        # - if moving ALONG desired trajectory
        self.update_target_pos(curr_pos)

        # update cmd from PID based on current position
        self.cmd = self.PID_control(curr_pos)

        #TODO change references to torque to velocity - I believe its just velocity
        # check if each angular torque is within set limits
        for i in range(7):
            if self.cmd[i][i] > self.max_cmd[i][i]:
                self.cmd[i][i] = self.max_cmd[i][i]
            if self.cmd[i][i] < -self.max_cmd[i][i]:
                self.cmd[i][i] = -self.max_cmd[i][i]        

    def update_target_pos(self, curr_pos):
        """
        Takes the current position of the robot. Determines what the next
        target position to move to should be depending on:
        - if robot is moving to start of desired trajectory or
        - if robot is moving along the desired trajectory
        """

        # check if the arm is at the start of the path to execute
        if not self.reached_start:
            #
            print "not reached start"
            #print "curr_pos", curr_pos, "self.goal", self.goal

            dist_from_start = PIDController.shortest_angular_distance(curr_pos, self.start)
            dist_from_start = np.abs(dist_from_start) #TODO this waas fabs but my version of numpy doeesnt have fabs
            print dist_from_start, "distance from start"

            # if all joints are close enough, robot is at start
            is_at_start = np.all(dist_from_start < START_EPSILON)

            # if any joint is too far, throw an error
            is_too_far = np.any(dist_from_start > START_MAX_DIST)
            if is_too_far:
                #TODO this should be more principled
                rospy.logfatal(ValueError("current joint angles: {} are too far from the trajectory start: {}".format(curr_pos, self.start)))
                rospy.signal_shutdown("reason")

            if is_at_start:
                self.reached_start = True
                self.path_start_T = time.time()
            else:
                self.target_pos = self.start.reshape((7, 1))
        else:
            t = time.time() - self.path_start_T

            self.target_pos = self.interpolate_trajectory(t)

            if not self.reached_goal:

                dist_from_goal = PIDController.shortest_angular_distance(curr_pos, self.goal)
                is_at_goal = np.all(np.abs(dist_from_goal) < GOAL_EPSILON)
                if is_at_goal:
                    self.reached_goal = True

    def interpolate_trajectory(self, time):
        if time >= self.trajectory_time:
            #if more than the trajectory time has passed, the target position is the final waypoint
            target_pos = self.trajectory[-1]
        else:
            print time
            #if time is not past the trajectory time, find the proper waypoint
            while self.time_points[self.target_index] < time:
                self.target_index += 1
            prev_t = self.time_points[self.target_index - 1]
            next_t = self.time_points[self.target_index]
            delta_t = next_t - prev_t

            prev_p = self.trajectory[self.target_index - 1]
            next_p = self.trajectory[self.target_index]
            delta_p = next_p - prev_p

            diff = delta_p * (time - prev_t) / delta_t
            target_pos = diff + prev_p

        return np.array(target_pos).reshape((7, 1))

    def fix_joint_angles(self, trajectory):
        #TODO is off by pi an issue with openRAVE or kinova? 
        # should I fix for kinova as well? TBD
        trajectory = trajectory.copy()
        for dof in trajectory:
            dof[2] -= np.pi
        return trajectory[:,:7]
    
    @staticmethod
    def shortest_angular_distance(angle1, angle2):
        return -((angle1 - angle2 + np.pi) % (2 * np.pi) - np.pi) 

def shortest_angular_distance_test():
    """ test a few cases to see if the shortest distance function is working
    """
    pi = np.pi
    tests = [[0, pi, pi], [0, pi/2, pi/2], [0, 2*pi, 0], [pi/2, 3*pi, pi/2]]
    for test in tests:
        res = test[2]
        test1_res = PIDController.shortest_angular_distance(test[0], test[1])
        test2_res = PIDController.shortest_angular_distance(test[1], test[0])
        print "res {}, test1_res, {}, test2_res {}".format(res, test1_res, test2_res)

if __name__ == '__main__':
    #argument = ' '.join(sys.argv[1:])
    controller = PIDController()
    #pnt = np.asarray([180]*7)*(np.pi/180)
    #home = np.asarray([80.363975525, 197.091796875, 179.857910156, 43.4620018005, -94.3617858887, 257.270996094, 287.989074707])*(np.pi/180)
    #traj1 = np.vstack([home, pnt])
    #traj2 = np.vstack([pnt, home])

    #print traj
    #controller.execute_trajectory(traj1)
    #controller.execute_trajectory(traj2)
